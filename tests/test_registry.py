import pytest

from android_watcher.registry import Registry


def test_register_and_get():
	reg: Registry[object] = Registry("detector")

	@reg.register("dummy")
	class Dummy:
		pass

	assert reg.get("dummy") is Dummy
	assert reg.available() == ["dummy"]


def test_available_is_sorted():
	reg: Registry[object] = Registry("notifier")

	@reg.register("zeta")
	class Z:
		pass

	@reg.register("alpha")
	class A:
		pass

	assert reg.available() == ["alpha", "zeta"]


def test_unknown_name_lists_available():
	reg: Registry[object] = Registry("triager")

	@reg.register("noop")
	class NoOp:
		pass

	with pytest.raises(KeyError) as exc:
		reg.get("bogus")
	msg = str(exc.value)
	assert "triager" in msg
	assert "bogus" in msg
	assert "noop" in msg  # lists available names


def test_duplicate_registration_rejected():
	reg: Registry[object] = Registry("detector")

	@reg.register("dup")
	class One:
		pass

	with pytest.raises(ValueError):

		@reg.register("dup")
		class Two:
			pass
