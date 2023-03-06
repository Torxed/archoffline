import importlib.util
import sys
import pathlib

# Load .git version before the builtin version
if pathlib.Path('./archoffline/__init__.py').absolute().exists():
	if spec := importlib.util.spec_from_file_location("archoffline", "./archoffline/__init__.py"):
		archoffline = importlib.util.module_from_spec(spec)
		sys.modules["archoffline"] = archoffline
		if spec.loader:
			spec.loader.exec_module(sys.modules["archoffline"])
else:
	import archoffline

if __name__ == '__main__':
	archoffline.main()