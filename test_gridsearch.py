from tune_sklearn import TuneRandomizedSearchCV, TuneGridSearchCV
from scipy.stats import randint, uniform
from sklearn.model_selection import GridSearchCV
from ray import tune
import numpy as np
from numpy.testing import (
    assert_almost_equal,
    assert_array_almost_equal,
    assert_array_equal,
)
import scipy.sparse as sp
from sklearn.model_selection import (
    GroupKFold,
    GroupShuffleSplit,
    KFold,
    LeaveOneGroupOut,
    LeavePGroupsOut,
    StratifiedKFold,
    StratifiedShuffleSplit,
)
from sklearn.datasets import (
    make_blobs,
    make_classification,
    make_multilabel_classification,
)
from sklearn.metrics import accuracy_score, f1_score, make_scorer, roc_auc_score
from sklearn.base import BaseEstimator
import pytest
from sklearn.cluster import KMeans
from sklearn.svm import SVC, LinearSVC
from sklearn import datasets
from sklearn.model_selection import train_test_split
from sklearn import linear_model
from sklearn.exceptions import NotFittedError
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.neighbors import KernelDensity
from ray.tune.schedulers import MedianStoppingRule
import unittest
from test_utils import (
    MockClassifier,
    CheckingClassifier,
    BrokenClassifier,
    MockDataFrame

)


class LinearSVCNoScore(LinearSVC):
    """An LinearSVC classifier that has no score method."""

    @property
    def score(self):
        raise AttributeError

X = np.array([[-1, -1], [-2, -1], [1, 1], [2, 1]])
y = np.array([1, 1, 2, 2])

class GridSearchTest(unittest.TestCase):
    def test_grid_search(self):
        # Test that the best estimator contains the right value for foo_param
        clf = MockClassifier()
        grid_search = TuneGridSearchCV(clf, {"foo_param": [1, 2, 3]})
        # make sure it selects the smallest parameter in case of ties
        grid_search.fit(X, y)
        self.assertEqual(grid_search.best_estimator_.foo_param, 2)

        assert_array_equal(grid_search.cv_results_["param_foo_param"].data, [1, 2, 3])

        # Smoke test the score etc:
        grid_search.score(X, y)
        grid_search.predict_proba(X)
        grid_search.decision_function(X)
        grid_search.transform(X)

        # Test exception handling on scoring
        grid_search.scoring = "sklearn"
        with self.assertRaises(ValueError):
            grid_search.fit(X, y)

    def test_grid_search_no_score(self):
        # Test grid-search on classifier that has no score function.
        clf = LinearSVC(random_state=0)
        X, y = make_blobs(random_state=0, centers=2)
        Cs = [0.1, 1, 10]
        clf_no_score = LinearSVCNoScore(random_state=0)

        # XXX: It seems there's some global shared state in LinearSVC - fitting
        # multiple `SVC` instances in parallel using threads sometimes results in
        # wrong results. This only happens with threads, not processes/sync.
        # For now, we'll fit using the sync scheduler.
        grid_search = TuneGridSearchCV(clf, {"C": Cs}, scoring="accuracy", scheduler=MedianStoppingRule())
        grid_search.fit(X, y)

        grid_search_no_score = TuneGridSearchCV(
            clf_no_score, {"C": Cs}, scoring="accuracy", scheduler=MedianStoppingRule()
        )
        # smoketest grid search
        grid_search_no_score.fit(X, y)

        # check that best params are equal
        self.assertEqual(grid_search_no_score.best_params, grid_search.best_params_)
        # check that we can call score and that it gives the correct result
        self.assertEqual(grid_search.score(X, y), grid_search_no_score.score(X, y))

        # giving no scoring function raises an error
        grid_search_no_score = TuneGridSearchCV(clf_no_score, {"C": Cs})
        with self.assertRaises(TypeError) as exc:
            grid_search_no_score.fit([[1]])

        self.assertTrue("no scoring" in str(exc.value))

    @pytest.mark.parametrize(
        "cls,kwargs", [(TuneGridSearchCV, {}), (TuneRandomizedSearchCV, {"iters": 1})]
    )
    def test_hyperparameter_searcher_with_fit_params(self, cls, kwargs):
        X = np.arange(100).reshape(10, 10)
        y = np.array([0] * 5 + [1] * 5)
        clf = CheckingClassifier(expected_fit_params=["spam", "eggs"])
        pipe = Pipeline([("clf", clf)])
        searcher = cls(pipe, {"clf__foo_param": [1, 2, 3]}, cv=2, **kwargs)

        # The CheckingClassifer generates an assertion error if
        # a parameter is missing or has length != len(X).
        with self.assertRaises(AssertionError) as exc:
            searcher.fit(X, y, clf__spam=np.ones(10))
        self.assertTrue("Expected fit parameter(s) ['eggs'] not seen." in str(exc.value))

        searcher.fit(X, y, clf__spam=np.ones(10), clf__eggs=np.zeros(10))
        ''' NOT YET SUPPORTED
        # Test with dask objects as parameters
        searcher.fit(
            X, y, clf__spam=da.ones(10, chunks=2), clf__eggs=dask.delayed(np.zeros(10))
        )
        '''

    def test_grid_search_score_method(self):
        X, y = make_classification(n_samples=100, n_classes=2, flip_y=0.2, random_state=0)
        clf = LinearSVC(random_state=0)
        grid = {"C": [0.1]}

        search_no_scoring = TuneGridSearchCV(clf, grid, scoring=None).fit(X, y)
        search_accuracy = TuneGridSearchCV(clf, grid, scoring="accuracy").fit(X, y)
        search_no_score_method_auc = TuneGridSearchCV(
            LinearSVCNoScore(), grid, scoring="roc_auc"
        ).fit(X, y)
        search_auc = TuneGridSearchCV(clf, grid, scoring="roc_auc").fit(X, y)

        # Check warning only occurs in situation where behavior changed:
        # estimator requires score method to compete with scoring parameter
        score_no_scoring = search_no_scoring.score(X, y)
        score_accuracy = search_accuracy.score(X, y)
        score_no_score_auc = search_no_score_method_auc.score(X, y)
        score_auc = search_auc.score(X, y)

        # ensure the test is sane
        self.assertTrue(score_auc < 1.0)
        self.assertTrue(score_accuracy < 1.0)
        self.assertTrue(score_auc != score_accuracy)

        assert_almost_equal(score_accuracy, score_no_scoring)
        assert_almost_equal(score_auc, score_no_score_auc)

    def test_grid_search_groups(self):
        # Check if ValueError (when groups is None) propagates to dcv.GridSearchCV
        # And also check if groups is correctly passed to the cv object
        rng = np.random.RandomState(0)

        X, y = make_classification(n_samples=15, n_classes=2, random_state=0)
        groups = rng.randint(0, 3, 15)

        clf = LinearSVC(random_state=0)
        grid = {"C": [1]}

        group_cvs = [
            LeaveOneGroupOut(),
            LeavePGroupsOut(2),
            GroupKFold(n_splits=3),
            GroupShuffleSplit(n_splits=3),
        ]
        for cv in group_cvs:
            gs = TuneGridSearchCV(clf, grid, cv=cv)

            with self.assertRaises(ValueError) as exc:
                assert gs.fit(X, y)
            self.assertTrue("parameter should not be None" in str(exc.value))

            gs.fit(X, y, groups=groups)

        non_group_cvs = [StratifiedKFold(n_splits=3), StratifiedShuffleSplit(n_splits=3)]
        for cv in non_group_cvs:
            gs = TuneGridSearchCV(clf, grid, cv=cv)
            # Should not raise an error
            gs.fit(X, y)

    '''
    @pytest.mark.xfail(reason="flaky test", strict=False)
    def test_return_train_score_warn(self):
        # Test that warnings are raised. Will be removed in sklearn 0.21
        X = np.arange(100).reshape(10, 10)
        y = np.array([0] * 5 + [1] * 5)
        X = (X - X.mean(0)) / X.std(0)  # help convergence
        grid = {"C": [0.1, 0.5]}

        for val in [True, False]:
            est = dcv.GridSearchCV(
                LinearSVC(random_state=0, tol=0.5), grid, return_train_score=val
            )
            with pytest.warns(None) as warns:
                results = est.fit(X, y).cv_results_
            assert not warns
            assert type(results) is dict

        est = dcv.GridSearchCV(LinearSVC(random_state=0), grid)
        with pytest.warns(None) as warns:
            results = est.fit(X, y).cv_results_
        assert not warns

        train_keys = {
            "split0_train_score",
            "split1_train_score",
            "split2_train_score",
            "mean_train_score",
            "std_train_score",
        }

        include_train_score = SK_VERSION <= packaging.version.parse("0.21.dev0")

        if include_train_score:
            assert all(x in results for x in train_keys)
        else:
            result = train_keys & set(results)
            assert result == {}

        for key in results:
            if key in train_keys:
                with pytest.warns(FutureWarning):
                    results[key]
            else:
                with pytest.warns(None) as warns:
                    results[key]
                assert not warns
    '''

    @pytest.mark.filterwarnings("ignore::sklearn.exceptions.ConvergenceWarning")
    def test_classes__property(self):
        # Test that classes_ property matches best_estimator_.classes_
        X = np.arange(100).reshape(10, 10)
        y = np.array([0] * 5 + [1] * 5)
        Cs = [0.1, 1, 10]

        grid_search = TuneGridSearchCV(LinearSVC(random_state=0), {"C": Cs})
        grid_search.fit(X, y)
        assert_array_equal(grid_search.best_estimator_.classes_, grid_search.classes_)

        # Test that regressors do not have a classes_ attribute
        grid_search = TuneGridSearchCV(Ridge(), {"alpha": [1.0, 2.0]})
        grid_search.fit(X, y)
        self.assertFalse(hasattr(grid_search, "classes_"))

        # Test that the grid searcher has no classes_ attribute before it's fit
        grid_search = TuneGridSearchCV(LinearSVC(random_state=0), {"C": Cs})
        self.assertFalse(hasattr(grid_search, "classes_"))

        # Test that the grid searcher has no classes_ attribute without a refit
        grid_search = TuneGridSearchCV(LinearSVC(random_state=0), {"C": Cs}, refit=False)
        grid_search.fit(X, y)
        self.assertFalse(hasattr(grid_search, "classes_"))

    def test_trivial_cv_results_attr(self):
        # Test search over a "grid" with only one point.
        # Non-regression test: grid_scores_ wouldn't be set by dcv.GridSearchCV.
        clf = MockClassifier()
        grid_search = TuneGridSearchCV(clf, {"foo_param": [1]})
        grid_search.fit(X, y)
        self.assertTrue(hasattr(grid_search, "cv_results_"))

        random_search = TuneRandomizedSearchCV(clf, {"foo_param": [0]}, iters=1)
        random_search.fit(X, y)
        self.assertTrue(hasattr(grid_search, "cv_results_"))

    def test_no_refit(self):
        # Test that GSCV can be used for model selection alone without refitting
        clf = MockClassifier()
        grid_search = TuneGridSearchCV(clf, {"foo_param": [1, 2, 3]}, refit=False)
        grid_search.fit(X, y)
        self.assertFalse(hasattr(grid_search, "best_estimator_"))
        self.assertFalse(hasattr(grid_search, "best_index_"))
        self.assertFalse(hasattr(grid_search, "best_score_"))
        self.assertFalse(hasattr(grid_search, "best_params_"))

        # Make sure the predict/transform etc fns raise meaningfull error msg
        for fn_name in (
            "predict",
            "predict_proba",
            "predict_log_proba",
            "transform",
            "inverse_transform",
        ):
            with self.assertRaises(NotFittedError) as exc:
                getattr(grid_search, fn_name)(X)
            self.assertTrue(
            (
                "refit=False. %s is available only after refitting on the "
                "best parameters" % fn_name
            ) in str(exc.value))
    '''NOT YET SUPPORTED
    def test_no_refit_multiple_metrics():
        clf = DecisionTreeClassifier()
        scoring = {"score_1": "accuracy", "score_2": "accuracy"}

        gs = dcv.GridSearchCV(clf, {"max_depth": [1, 2, 3]}, refit=False, scoring=scoring)
        gs.fit(da_X, da_y)
        assert not hasattr(gs, "best_estimator_")
        assert not hasattr(gs, "best_index_")
        assert not hasattr(gs, "best_score_")
        assert not hasattr(gs, "best_params_")

        for fn_name in ("predict", "predict_proba", "predict_log_proba"):
            with pytest.raises(NotFittedError) as exc:
                getattr(gs, fn_name)(X)
            assert (
                "refit=False. %s is available only after refitting on the "
                "best parameters" % fn_name
            ) in str(exc.value)
    '''

    def test_grid_search_error(self):
        # Test that grid search will capture errors on data with different length
        X_, y_ = make_classification(n_samples=200, n_features=100, random_state=0)

        clf = LinearSVC()
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]})
        with self.assertRaises(ValueError):
            cv.fit(X_[:180], y_)

    def test_grid_search_one_grid_point(self):
        X_, y_ = make_classification(n_samples=200, n_features=100, random_state=0)
        param_dict = {"C": [1.0], "kernel": ["rbf"], "gamma": [0.1]}

        clf = SVC()
        cv = TuneGridSearchCV(clf, param_dict)
        cv.fit(X_, y_)

        clf = SVC(C=1.0, kernel="rbf", gamma=0.1)
        clf.fit(X_, y_)

        assert_array_equal(clf.dual_coef_, cv.best_estimator_.dual_coef_)

    def test_grid_search_bad_param_grid(self):
        param_dict = {"C": 1.0}
        clf = SVC()

        with self.assertRaises(ValueError) as exc:
            TuneGridSearchCV(clf, param_dict)
        self.assertTrue(
        (
            "Parameter values for parameter (C) need to be a sequence"
            "(but not a string) or np.ndarray."
        ) in str(exc.value))

        param_dict = {"C": []}
        clf = SVC()

        with self.assertRaises(ValueError) as exc:
            TuneGridSearchCV(clf, param_dict)
        self.assertTrue(
        (
            "Parameter values for parameter (C) need to be a non-empty " "sequence."
        ) in str(exc.value))

        param_dict = {"C": "1,2,3"}
        clf = SVC()

        with self.assertRaises(ValueError) as exc:
            TuneGridSearchCV(clf, param_dict)
        self.assertTrue(
        (
            "Parameter values for parameter (C) need to be a sequence"
            "(but not a string) or np.ndarray."
        ) in str(exc.value))

        param_dict = {"C": np.ones(6).reshape(3, 2)}
        clf = SVC()
        with self.assertRaises(ValueError):
            TuneGridSearchCV(clf, param_dict)

    def test_grid_search_sparse(self):
        # Test that grid search works with both dense and sparse matrices
        X_, y_ = make_classification(n_samples=200, n_features=100, random_state=0)

        clf = LinearSVC()
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]})
        cv.fit(X_[:180], y_[:180])
        y_pred = cv.predict(X_[180:])
        C = cv.best_estimator_.C

        X_ = sp.csr_matrix(X_)
        clf = LinearSVC()
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]})
        cv.fit(X_[:180].tocoo(), y_[:180])
        y_pred2 = cv.predict(X_[180:])
        C2 = cv.best_estimator_.C

        self.assertTrue(np.mean(y_pred == y_pred2) >= 0.9)
        self.assertEqual(C, C2)

    def test_grid_search_sparse_scoring(self):
        X_, y_ = make_classification(n_samples=200, n_features=100, random_state=0)

        clf = LinearSVC()
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]}, scoring="f1")
        cv.fit(X_[:180], y_[:180])
        y_pred = cv.predict(X_[180:])
        C = cv.best_estimator_.C

        X_ = sp.csr_matrix(X_)
        clf = LinearSVC()
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]}, scoring="f1")
        cv.fit(X_[:180], y_[:180])
        y_pred2 = cv.predict(X_[180:])
        C2 = cv.best_estimator_.C

        assert_array_equal(y_pred, y_pred2)
        self.assertEqual(C, C2)
        # Smoke test the score
        # np.testing.assert_allclose(f1_score(cv.predict(X_[:180]), y[:180]),
        #                            cv.score(X_[:180], y[:180]))

        # test loss where greater is worse
        def f1_loss(y_true_, y_pred_):
            return -f1_score(y_true_, y_pred_)

        F1Loss = make_scorer(f1_loss, greater_is_better=False)
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]}, scoring=F1Loss)
        cv.fit(X_[:180], y_[:180])
        y_pred3 = cv.predict(X_[180:])
        C3 = cv.best_estimator_.C

        self.assertEqual(C, C3)
        assert_array_equal(y_pred, y_pred3)

    def test_grid_search_precomputed_kernel(self):
        # Test that grid search works when the input features are given in the
        # form of a precomputed kernel matrix
        X_, y_ = make_classification(n_samples=200, n_features=100, random_state=0)

        # compute the training kernel matrix corresponding to the linear kernel
        K_train = np.dot(X_[:180], X_[:180].T)
        y_train = y_[:180]

        clf = SVC(kernel="precomputed")
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]})
        cv.fit(K_train, y_train)

        self.assertTrue(cv.best_score_ >= 0)

        # compute the test kernel matrix
        K_test = np.dot(X_[180:], X_[:180].T)
        y_test = y_[180:]

        y_pred = cv.predict(K_test)

        self.assertTrue(np.mean(y_pred == y_test) >= 0)

        # test error is raised when the precomputed kernel is not array-like
        # or sparse
        with self.assertRaises(ValueError):
            cv.fit(K_train.tolist(), y_train)

    def test_grid_search_precomputed_kernel_error_nonsquare(self):
        # Test that grid search returns an error with a non-square precomputed
        # training kernel matrix
        K_train = np.zeros((10, 20))
        y_train = np.ones((10,))
        clf = SVC(kernel="precomputed")
        cv = TuneGridSearchCV(clf, {"C": [0.1, 1.0]})
        with self.assertRaises(ValueError):
            cv.fit(K_train, y_train)



    def test_refit(self):
        # Regression test for bug in refitting
        # Simulates re-fitting a broken estimator; this used to break with
        # sparse SVMs.
        X = np.arange(100).reshape(10, 10)
        y = np.array([0] * 5 + [1] * 5)

        clf = TuneGridSearchCV(
            BrokenClassifier(), [{"parameter": [0, 1]}], scoring="accuracy", refit=True
        )
        clf.fit(X, y)

    def test_gridsearch_nd(self):
        # Pass X as list in dcv.GridSearchCV
        X_4d = np.arange(10 * 5 * 3 * 2).reshape(10, 5, 3, 2)
        y_3d = np.arange(10 * 7 * 11).reshape(10, 7, 11)
        clf = CheckingClassifier(
            check_X=lambda x: x.shape[1:] == (5, 3, 2),
            check_y=lambda x: x.shape[1:] == (7, 11),
        )
        grid_search = TuneGridSearchCV(clf, {"foo_param": [1, 2, 3]})
        grid_search.fit(X_4d, y_3d).score(X, y)
        self.assertTrue(hasattr(grid_search, "cv_results_"))

    def test_X_as_list(self):
        # Pass X as list in dcv.GridSearchCV
        X = np.arange(100).reshape(10, 10)
        y = np.array([0] * 5 + [1] * 5)

        clf = CheckingClassifier(check_X=lambda x: isinstance(x, list))
        cv = KFold(n_splits=3)
        grid_search = TuneGridSearchCV(clf, {"foo_param": [1, 2, 3]}, cv=cv)
        grid_search.fit(X.tolist(), y).score(X, y)
        self.assertTrue(hasattr(grid_search, "cv_results_"))

    def test_y_as_list(self):
        # Pass y as list in dcv.GridSearchCV
        X = np.arange(100).reshape(10, 10)
        y = np.array([0] * 5 + [1] * 5)

        clf = CheckingClassifier(check_y=lambda x: isinstance(x, list))
        cv = KFold(n_splits=3)
        grid_search = TuneGridSearchCV(clf, {"foo_param": [1, 2, 3]}, cv=cv)
        grid_search.fit(X, y.tolist()).score(X, y)
        self.assertTrue(hasattr(grid_search, "cv_results_"))


    @pytest.mark.filterwarnings("ignore")
    def test_pandas_input(self):
        # check cross_val_score doesn't destroy pandas dataframe
        types = [(MockDataFrame, MockDataFrame)]
        try:
            from pandas import Series, DataFrame

            types.append((DataFrame, Series))
        except ImportError:
            pass

        X = np.arange(100).reshape(10, 10)
        y = np.array([0] * 5 + [1] * 5)

        for InputFeatureType, TargetType in types:
            # X dataframe, y series
            X_df, y_ser = InputFeatureType(X), TargetType(y)
            clf = CheckingClassifier(
                check_X=lambda x: isinstance(x, InputFeatureType),
                check_y=lambda x: isinstance(x, TargetType),
            )

            grid_search = TuneGridSearchCV(clf, {"foo_param": [1, 2, 3]})
            grid_search.fit(X_df, y_ser).score(X_df, y_ser)
            grid_search.predict(X_df)
            self.assertTrue(hasattr(grid_search, "cv_results_"))

    def test_unsupervised_grid_search(self):
        # test grid-search with unsupervised estimator
        X, y = make_blobs(random_state=0)
        km = KMeans(random_state=0)
        grid_search = TuneGridSearchCV(
            km, param_grid=dict(n_clusters=[2, 3, 4]), scoring="adjusted_rand_score"
        )
        grid_search.fit(X, y)
        # ARI can find the right number :)
        self.assertEqual(grid_search.best_params_["n_clusters"], 3)

        # Now without a score, and without y
        grid_search = TuneGridSearchCV(km, param_grid=dict(n_clusters=[2, 3, 4]))
        grid_search.fit(X)
        self.assertEqual(grid_search.best_params_["n_clusters"], 4)

    def test_gridsearch_no_predict(self):
        # test grid-search with an estimator without predict.
        # slight duplication of a test from KDE
        def custom_scoring(estimator, X):
            return 42 if estimator.bandwidth == 0.1 else 0

        X, _ = make_blobs(cluster_std=0.1, random_state=1, centers=[[0, 1], [1, 0], [0, 0]])
        search = TuneGridSearchCV(
            KernelDensity(),
            param_grid=dict(bandwidth=[0.01, 0.1, 1]),
            scoring=custom_scoring,
        )
        search.fit(X)
        self.assertEquals(search.best_params_["bandwidth"], 0.1)
        self.assertEquals(search.best_score_, 42)

    def test_check_cv_results_array_types(self, cv_results, param_keys, score_keys):
        # Check if the search `cv_results`'s array are of correct types
        self.assertTrue(all(isinstance(cv_results[param], np.ma.MaskedArray) for param in param_keys))
        self.assertTrue(all(cv_results[key].dtype == object for key in param_keys))
        self.assertFalse(any(isinstance(cv_results[key], np.ma.MaskedArray) for key in score_keys))
        self.assertTrue(
        all(
            cv_results[key].dtype == np.float64
            for key in score_keys
            if not key.startswith("rank")
        )
        )
        self.assertEquals(cv_results["rank_test_score"].dtype, np.int32)

    def test_check_cv_results_keys(self, cv_results, param_keys, score_keys, n_cand):
        # Test the search.cv_results_ contains all the required results
        assert_array_equal(
            sorted(cv_results.keys()), sorted(param_keys + score_keys + ("params",))
        )
        self.assertTrue(all(cv_results[key].shape == (n_cand,) for key in param_keys + score_keys))





    def test_digits(self):
        # Loading the Digits dataset
        digits = datasets.load_digits()

        # To apply an classifier on this data, we need to flatten the image, to
        # turn the data in a (samples, feature) matrix:
        n_samples = len(digits.images)
        X = digits.images.reshape((n_samples, -1))
        y = digits.target

        # Split the dataset in two equal parts
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.5, random_state=0)

        # Set the parameters by cross-validation
        tuned_parameters = {'kernel': ['rbf'], 'gamma': [1e-3, 1e-4],
                             'C': [1, 10, 100, 1000]}

        tune_search = TuneGridSearchCV(SVC(),
                               tuned_parameters,
                               scheduler=MedianStoppingRule(),
                               iters=20)
        tune_search.fit(X_train, y_train)

        pred = tune_search.predict(X_test)
        print(pred)
        accuracy = np.count_nonzero(np.array(pred) == np.array(y_test))/len(pred)
        print(accuracy)

    def test_diabetes(self):
        # load the diabetes datasets
        dataset = datasets.load_diabetes()
        X = dataset.data
        y = dataset.target
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.5, random_state=0)
        # prepare a range of alpha values to test
        alphas = np.array([1,0.1,0.01,0.001,0.0001,0])
        param_grid = dict(alpha=alphas)
        # create and fit a ridge regression model, testing each alpha
        model = linear_model.Ridge()

        tune_search = TuneGridSearchCV(model, param_grid, MedianStoppingRule())
        tune_search.fit(X_train, y_train)

        pred = tune_search.predict(X_test)
        print(pred)
        error = sum(np.array(pred) - np.array(y_test))/len(pred)
        print(error)


if __name__ == '__main__':
    unittest.main()