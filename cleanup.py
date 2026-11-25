import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    from qishui_auto import cleanup_images
    cleanup_images()
except Exception:
    pass
