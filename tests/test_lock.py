import pytest

from android_watcher.lock import AlreadyRunning, run_lock


def test_lock_acquire_release(tmp_path):
	with run_lock(str(tmp_path)):
		assert (tmp_path / "run.lock").exists()
	# After exit, re-acquiring must succeed.
	with run_lock(str(tmp_path)):
		pass


def test_lock_conflict_raises(tmp_path):
	with run_lock(str(tmp_path)):
		with pytest.raises(AlreadyRunning):
			with run_lock(str(tmp_path)):
				pass


def test_lock_creates_data_dir(tmp_path):
	nested = tmp_path / "does" / "not" / "exist"
	with run_lock(str(nested)):
		assert (nested / "run.lock").exists()
