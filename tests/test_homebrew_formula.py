from pathlib import Path

FORMULA = Path(__file__).resolve().parents[1] / "Formula/android-watcher.rb"


def test_formula_exists():
	assert FORMULA.is_file()


def test_uses_python_virtualenv():
	text = FORMULA.read_text()
	assert "class AndroidWatcher < Formula" in text
	assert "include Language::Python::Virtualenv" in text
	assert "virtualenv_install_with_resources" in text


def test_pins_main_package_url_and_sha():
	text = FORMULA.read_text()
	assert "files.pythonhosted.org" in text  # sdist from PyPI
	assert "sha256" in text
	assert 'depends_on "python@3.11"' in text or 'depends_on "python@3.12"' in text


def test_has_test_block():
	text = FORMULA.read_text()
	assert "test do" in text
