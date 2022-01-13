import statistics
import numpy as np
import pandas as pd
import scipy.stats
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import KernelDensity
from molten.drift_detector import DriftDetector
from molten.other.page_hinkley import PageHinkley


class PCACD(DriftDetector):
    """Principal Component Analysis Change Detection (PCA-CD) is a drift
    detection algorithm which checks for change in the distribution of the given
    data using one of several divergence metrics calculated on the data's
    principal components.

    First, principal components are built from the reference window - the
    initial window_size samples. New samples from the test window, of the same
    width, are projected onto these principal components. The divergence metric
    is calculated on these scores for the reference and test windows; if this
    metric diverges enough, then we consider drift to have occurred. This
    threshold is determined dynamically through the use of the Page-Hinkley test.

    Once drift is detected, the reference window is replaced with the current
    test window, and the test window is initialized.

    Ref. Qahtan, A., Wang, S. A PCA-Based Change Detection Framework for
    Multidimensional Data Streams Categories and Subject Descriptors. KDD '15:
    The 21st ACM SIGKDD International Conference on Knowledge Discovery and Data
    Mining, 935-44. https://doi.org/10.1145/2783258.2783359

    Attributes:
        total_samples (int): number of samples the drift detector has ever
            been updated with
        samples_since_reset (int): number of samples since the last time the
            drift detector was reset
        drift_state (str): detector's current drift state. Can take values
            "drift", "warning", or None.
        step (int): how frequently (by number of samples), to detect drift.
            This is either 100 samples or sample_period * window_size, whichever
            is smaller.
        ph_threshold (float): threshold parameter for the internal Page-Hinkley
            detector. Takes the value of .01 * window_size.
        num_pcs (int): the number of principal components being used to meet
            the specified ev_threshold parameter.
    """

    def __init__(
        self,
        window_size,
        ev_threshold=0.99,
        delta=0.1,
        divergence_metric="kl",
        sample_period=0.05,
        online_scaling=False,
        track_state=False,
    ):
        """
        Args:
            window_size (int): size of the reference window. Note that PCA_CD
                will only try to detect drift periodically, either every 100
                observations or 5% of the window_size, whichever is smaller.
            ev_threshold (float, optional): Threshold for percent explained
                variance required when selecting number of principal components.
                Defaults to 0.99.
            delta (float, optional): Parameter for Page Hinkley test. Minimum
                amplitude of change in data needed to sound alarm. Defaults to 0.1.
            divergence_metric (str, optional): divergence metric when comparing
                the two distributions when detecting drift. Defaults to "kl".
                    "kl" - modified Kullback-Leibler divergence, uses kernel density estimation with Epanechnikov
                    kernel
                    "llh" - log-likelihood, uses kernel density estimation with Epanechnikov kernel
                    "intersection" - intersection area under the curves for the
                    estimated density functions, uses histograms to estimate densities of windows. A discontinuous,
                    less accurate estimate that should only be used when efficiency is of concern.
            sample_period (float, optional): how often to check for drift. This
                is 100 samples or sample_period * window_size, whichever is
                smaller. Default .05, or 5% of the window size.
            online_scaling (bool, optional): whether to standardize the data as
                it comes in, using the reference window, before applying PCA.
                Defaults to False.
            track_state (bool, optional): whether to store the status of the
                Page Hinkley detector every time drift is identified.
                Defaults to False.
        """
        super().__init__()
        self.window_size = window_size
        self.ev_threshold = ev_threshold
        self.divergence_metric = divergence_metric
        self.track_state = track_state
        self.sample_period = (
            sample_period  # TODO modify sample period dependent upon density estimate
        )

        # Initialize parameters
        self.step = min(100, round(self.sample_period * window_size))
        self.ph_threshold = round(0.01 * window_size)
        self.bins = int(np.floor(np.sqrt(self.window_size)))
        self.delta = delta

        self._drift_detection_monitor = PageHinkley(
            delta=self.delta, threshold=self.ph_threshold, burn_in=0
        )
        if self.track_state:
            self._drift_tracker = pd.DataFrame()

        self.num_pcs = None

        self.online_scaling = online_scaling
        if self.online_scaling is True:
            self._reference_scaler = StandardScaler()

        self._build_reference_and_test = True
        self._reference_window = pd.DataFrame()
        self._test_window = pd.DataFrame()
        self._pca = None
        self._reference_pca_projection = pd.DataFrame()
        self._test_pca_projection = pd.DataFrame()
        self._density_reference = {}
        self._change_score = [0]

    def update(self, next_obs, *args, **kwargs):  # pylint: disable=arguments-differ
        """Update the detector with a new observation.

        Args:
            next_obs: next observation, as a pandas Series
        """

        if self._build_reference_and_test:
            if self.drift_state is not None:
                self._reference_window = self._test_window.copy()
                if self.online_scaling is True:
                    # we'll need to refit the scaler. this occurs when both reference and test
                    # windows are full, so, inverse_transform first, here
                    self._reference_window = pd.DataFrame(
                        self._reference_scaler.inverse_transform(self._reference_window)
                    )
                self._test_window = pd.DataFrame()
                self.reset()
                self._drift_detection_monitor.reset()

            elif len(self._reference_window) < self.window_size:
                self._reference_window = self._reference_window.append(next_obs)

            elif len(self._test_window) < self.window_size:
                self._test_window = self._test_window.append(next_obs)

            if len(self._test_window) == self.window_size:
                self._build_reference_and_test = False

                # Fit Reference window onto PCs
                if self.online_scaling is True:
                    self._reference_window = pd.DataFrame(
                        self._reference_scaler.fit_transform(self._reference_window)
                    )
                    self._test_window = pd.DataFrame(
                        self._reference_scaler.transform(self._test_window)
                    )

                # Compute principal components
                self._pca = PCA(self.ev_threshold)
                self._pca.fit(self._reference_window)
                self.num_pcs = len(self._pca.components_)

                # Project Reference window onto PCs
                self._reference_pca_projection = pd.DataFrame(
                    self._pca.transform(self._reference_window),
                )

                # Project test window onto PCs
                self._test_pca_projection = pd.DataFrame(
                    self._pca.transform(self._test_window),
                )

                # Compute reference distribution
                for i in range(self.num_pcs):

                    if self.divergence_metric == "intersection":
                        # Histograms need the same bin edges so find bounds from both windows to inform range
                        lower = min(
                            self._reference_pca_projection.iloc[:, i].append(
                                self._test_pca_projection.iloc[:, i]
                            )
                        )

                        upper = max(
                            self._reference_pca_projection.iloc[:, i].append(
                                self._test_pca_projection.iloc[:, i]
                            )
                        )

                        self._density_reference[f"PC{i + 1}"] = self._build_histograms(
                            self._reference_pca_projection.iloc[:, i],
                            bins=self.bins,
                            bin_range=(lower, upper),
                        )

                    else:
                        self._density_reference[f"PC{i + 1}"] = self._build_kde(
                            self._reference_pca_projection.iloc[:, i]
                        )

        else:

            # Add new obs to test window
            if self.online_scaling is True:
                next_obs = pd.DataFrame(self._reference_scaler.transform(next_obs))
            self._test_window = self._test_window.iloc[1:, :].append(next_obs)

            # Project new observation onto PCs
            next_proj = pd.DataFrame(
                self._pca.transform(np.array(next_obs).reshape(1, -1)),
            )

            # Add projection to test projection data
            self._test_pca_projection = self._test_pca_projection.iloc[1:, :].append(
                next_proj, ignore_index=True
            )

            # Compute change score
            if (self.total_samples % self.step) == 0 and self.total_samples != 0:

                # Compute density distribution for test data
                self._density_test = {}
                for i in range(self.num_pcs):

                    if self.divergence_metric == "intersection":
                        # Histograms need the same bin edges so find bounds from both windows to inform range
                        lower = min(
                            self._reference_pca_projection.iloc[:, i].append(
                                self._test_pca_projection.iloc[:, i]
                            )
                        )

                        upper = max(
                            self._reference_pca_projection.iloc[:, i].append(
                                self._test_pca_projection.iloc[:, i]
                            )
                        )
                        self._density_test[f"PC{i + 1}"] = self._build_histograms(
                            self._test_pca_projection.iloc[:, i],
                            bins=self.bins,
                            bin_range=(lower, upper),
                        )

                    elif self.divergence_metric == "kl":
                        self._density_test[f"PC{i + 1}"] = self._build_kde(
                            self._test_pca_projection.iloc[:, i]
                        )

                    # if LLH, no estimates of test density is needed

                # Compute current score
                change_scores = []

                if self.divergence_metric == "kl":
                    for i in range(self.num_pcs):
                        change_scores.append(
                            self._modified_kl_divergence(
                                self._reference_pca_projection.iloc[:, i],
                                self._test_pca_projection.iloc[:, i],
                                self._density_reference[f"PC{i + 1}"],
                                self._density_test[f"PC{i + 1}"],
                            )
                        )

                elif self.divergence_metric == "llh":
                    for i in range(self.num_pcs):
                        change_scores.append(
                            self._llh_divergence(
                                self._density_reference[f"PC{i + 1}"],
                                self._test_pca_projection.iloc[:, i],
                            )
                        )

                elif self.divergence_metric == "intersection":
                    for i in range(self.num_pcs):
                        change_scores.append(
                            self._intersection_divergence(
                                self._density_reference[f"PC{i + 1}"],
                                self._density_test[f"PC{i + 1}"],
                            )
                        )

                change_score = max(change_scores)
                self._change_score.append(change_score)

                self._drift_detection_monitor.update(
                    next_obs=change_score, obs_id=next_obs.index.values[0]
                )

                if self._drift_detection_monitor.drift_state is not None:
                    self._build_reference_and_test = True
                    self.drift_state = "drift"
                    if self.track_state:
                        self._drift_tracker = self._drift_tracker.append(
                            self._drift_detection_monitor.to_dataframe()
                        )

        super().update()

    @staticmethod
    def _epanechnikov_kernel(x_j):
        """Calculate the Epanechnikov kernel value for a given value x_j, for
        use in kernel density estimation.

        Args:
            x_j: single value

        Returns:
            Epanechnikov kernel value for x_j.

        """
        if abs(x_j) <= 1:
            return (3 / 4) * (1 - (x_j ** 2))
        else:
            return 0

    @classmethod
    def _build_kde(cls, sample):
        """Compute the Kernel Density Estimate for a given 1D data stream

        Args:
            sample: 1D data for which we desire to estimate its density function

        Returns:
            Dict with density estimates for each value and KDE object

        """

        # sample_length = len(values)
        # bandwidth = 1.06 * statistics.stdev(values) * (sample_length ** (-1 / 5))
        # density = [
        #    (1 / (sample_length * bandwidth))
        #    * sum([cls._epanechnikov_kernel((x - x_j) / bandwidth) for x_j in values])
        #    for x in values
        # ]

        sample_length = len(sample)
        bandwidth = 1.06 * statistics.stdev(sample) * (sample_length ** (-1 / 5))
        kde_object = KernelDensity(bandwidth=bandwidth, kernel="epanechnikov").fit(
            sample.values.reshape(-1, 1)
        )
        # score_samples gives log-likelihood for each point, true density values should be > 0 so exponentiate
        density = np.exp(kde_object.score_samples(sample.values.reshape(-1, 1)))

        return {"density": density, "object": kde_object}

    @staticmethod
    def _build_histograms(sample, bins, bin_range):
        """Compute the histogram density estimates for a given 1D data stream. Density estimates consist of the value of
        the pdf in each bin, normalized s.t. integral over the entire range is 1

                Args:
                    sample: 1D array in which we desire to estimate its density function
                    bins: number of bins for estimating histograms. Equal to sqrt of cardinality of ref window
                    bin_range: (float, float) lower and upper bound of histogram bins

                Returns:
                    Dict of bin edges and corresponding density values (normalized s.t. they sum to 1)

        """

        density = np.histogram(sample, bins=bins, range=bin_range, density=True)
        return {
            "bin_edges": list(density[1]),
            "density": list(density[0] / np.sum(density[0])),
        }

    @classmethod
    def _modified_kl_divergence(
        cls, ref_pca_projection, test_pca_projection, density_reference, density_test
    ):
        """Computes Kullback-Leibler divergence between two distributions

        Args:
            ref_pca_projection (DataFrame): 1D values of PC from ref distribution
            test_pca_projection (DataFrame): 1D values of PC from test distribution
            density_reference (dict): dictionary of density values and object from ref distribution
            density_test (dict): dictionary of density values and object from test distribution

        Returns:
            Change Score

        """

        # append ref and test pca projections together
        pca_projection = ref_pca_projection.append(test_pca_projection)
        ref_estimator = density_reference["object"]
        test_estimator = density_test["object"]
        ref_estimates = np.exp(
            ref_estimator.score_samples(pca_projection.values.reshape(-1, 1))
        )
        test_estimates = np.exp(
            test_estimator.score_samples(pca_projection.values.reshape(-1, 1))
        )
        kl = max(
            scipy.stats.entropy(ref_estimates, test_estimates),
            scipy.stats.entropy(test_estimates, ref_estimates),
        )

        return kl

    @staticmethod
    def _intersection_divergence(density_reference, density_test):
        """Computes Intersection Area similarity between two distributions using histogram density estimation method.
        A value of 0 means the distributions are identical, a value of 1 means they are completely different

        Args:
            density_reference (dict): dictionary of density values from reference distribution
            density_test (dict): dictionary of density values from test distribution

        Returns:
            Change score

        """

        intersection = np.sum(
            np.minimum(density_reference["density"], density_test["density"])
        )
        divergence = 1 - intersection

        return divergence

    @staticmethod
    def _llh_divergence(density_reference, test_pca_projection):
        """Computes Log-Likelihood similarity between two distributions

        Args:
            density_reference (dict): dictionary of density and object values from ref distribution
            test_pca_projection (DataFrame): 1D values of PC from test distribution

        Returns:
            Change score

        """

        ref_density_estimates = density_reference["density"]
        ref_estimator = density_reference["object"]
        test_density_estimates = ref_estimator.score_samples(
            test_pca_projection.values.reshape(-1, 1)
        )

        total_llh_ref = np.sum(np.log(ref_density_estimates))
        total_llh_test = np.sum(test_density_estimates)

        divergence = np.abs(
            total_llh_test / len(ref_density_estimates)
            - total_llh_ref / len(test_pca_projection)
        )

        return divergence
