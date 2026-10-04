import tempfile

from redbot.core import data_manager

_test_data = tempfile.TemporaryDirectory(prefix="cactuscogs-tests-")
data_manager.basic_config = {
    "DATA_PATH": _test_data.name,
    "COG_PATH_APPEND": "cogs",
    "CORE_PATH_APPEND": "core",
    "STORAGE_TYPE": "JSON",
    "STORAGE_DETAILS": {},
}
data_manager._instance_name = "cactuscogs-tests"
