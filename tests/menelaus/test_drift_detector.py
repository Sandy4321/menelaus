"""Module for checking behavior of DriftDetector."""
from menelaus.drift_detector import DriftDetector, StreamingDetector, BatchDetector
import pytest
import numpy as np
import pandas as pd

# override this attribute so that it is instantiable
# hacky, but proper child classes doesn't cover the input_type setter.
DriftDetector.__abstractmethods__ = set()
StreamingDetector.__abstractmethods__ = set()
BatchDetector.__abstractmethods__ = set()


def test_drift_state_validation_streaming():
    """Ensure ValueError on invalid drift_state for StreamingDetector"""
    det = StreamingDetector()
    with pytest.raises(ValueError) as _:
        det.drift_state = "balloon"

    for state in ["drift", "warning", None]:
        det.drift_state = state
        assert det.drift_state == state


def test_drift_state_validation_batch():
    """Ensure ValueError on invalid drift_state for BatchDetector"""
    det = BatchDetector()
    with pytest.raises(ValueError) as _:
        det.drift_state = "balloon"

    for state in ["drift", "warning", None]:
        det.drift_state = state
        assert det.drift_state == state


def test_drift_state_validation():
    """Confirm that bad drift_state values raise ValueErrors."""
    det = DriftDetector()
    with pytest.raises(ValueError) as _:
        det.drift_state = "balloon"

    for state in ["drift", "warning", None]:
        det.drift_state = state
        assert det.drift_state == state


def test_streaming_validation_y_one_obs():
    det = StreamingDetector()
    with pytest.raises(ValueError) as _:
        det.update(X=None, y_true=np.array([[1], [1]]), y_pred=np.array([[0], [0]]))


def test_streaming_validation_X_one_obs():
    det = StreamingDetector()
    with pytest.raises(ValueError) as _:
        det.update(X=np.array([[5], [4]]), y_true=None, y_pred=None)


def test_streaming_validation_X_columns():
    det = StreamingDetector()
    input1 = pd.DataFrame({"a": [1], "b": [2]})
    input2 = pd.DataFrame({"c": [1], "d": [2]})
    det.update(input1, y_true=None, y_pred=None)
    with pytest.raises(ValueError) as _:
        det.update(input2, y_true=None, y_pred=None)


def test_streaming_validation_X_dimensions():
    det = StreamingDetector()
    input1 = np.array([1, 2, 3])
    input2 = pd.DataFrame(input1.reshape(1, -1), columns=["a", "b", "c"])
    input3 = input2.values
    input4 = np.array([1, 2, 3, 4])

    det.update(input1, y_true=None, y_pred=None)
    det.update(input2, y_true=None, y_pred=None)
    det.update(input3, y_true=None, y_pred=None)
    with pytest.raises(ValueError) as _:
        det.update(input4, y_true=None, y_pred=None)


def test_batch_validation_y_one_column():
    det = BatchDetector()
    det.update(X=None, y_true=1, y_pred=[1])
    with pytest.raises(ValueError) as _:
        det.update(
            X=None, y_true=np.array([[1, 2], [1, 2]]), y_pred=np.array([[0], [0]])
        )


def test_batch_validation_X_columns():
    det = BatchDetector()
    input1 = pd.DataFrame({"a": [1], "b": [2]})
    input2 = pd.DataFrame({"c": [1], "d": [2]})
    det.update(input1, y_true=None, y_pred=None)
    with pytest.raises(ValueError) as _:
        det.update(input2, y_true=None, y_pred=None)


def test_batch_validation_X_dimensions():
    det = BatchDetector()
    input1 = np.array([1, 2, 3])
    input2 = pd.DataFrame(input1.reshape(1, -1), columns=["a", "b", "c"])
    input3 = input2.values
    input4 = np.array([1, 2, 3, 4])

    det.update(input1, y_true=None, y_pred=None)
    det.update(input2, y_true=None, y_pred=None)
    det.update(input3, y_true=None, y_pred=None)
    with pytest.raises(ValueError) as _:
        det.update(input4, y_true=None, y_pred=None)


def test_input_type():
    det = DriftDetector()
    det._input_type = "stream"
    assert det.input_type == "stream"
