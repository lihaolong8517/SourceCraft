import os
import sys
import subprocess
import playwright_fix
from playwright_browser import get_python_with_playwright

def test_environment_setup():
    print("=== Testing environment setup ===")
    playwright_fix.setup_playwright_environment()
    playwright_fix.fix_windows_path_env()
    
    browsers_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    print(f"PLAYWRIGHT_BROWSERS_PATH: {browsers_path}")
    
    exe_path = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
    print(f"PLAYWRIGHT_CHROMIUM_EXECUTABLE: {exe_path}")
    
    if browsers_path and os.path.exists(browsers_path):
        print("[OK] Browsers directory found")
    else:
        print("[WARN] Browsers directory NOT found (expected in dev if not yet downloaded)")

def test_python_path():
    print("\n=== Testing python path resolution ===")
    py_path = get_python_with_playwright()
    print(f"Python with Playwright: {py_path}")
    
    # Simulate frozen
    original_frozen = getattr(sys, 'frozen', False)
    original_executable = sys.executable
    
    try:
        sys.frozen = True
        sys.executable = r"C:\Path\To\App.exe"
        # Reset cache
        import playwright_browser
        playwright_browser._CACHED_PYTHON_WITH_PLAYWRIGHT = None
        
        frozen_py_path = get_python_with_playwright()
        print(f"Frozen Python with Playwright: {frozen_py_path}")
        if frozen_py_path == sys.executable:
            print("[OK] Frozen path resolution works")
        else:
            print("[ERR] Frozen path resolution FAILED")
            
    finally:
        # Restore
        if hasattr(sys, 'frozen'):
            if original_frozen: sys.frozen = True
            else: del sys.frozen
        sys.executable = original_executable

def test_launcher_args():
    print("\n=== Testing launcher argument simulation ===")
    # This just checks if we can import and call run_launcher without immediate crash
    # We won't actually launch a browser here as it requires async loop and real playwright
    try:
        from playwright_browser import run_launcher
        print("[OK] run_launcher imported")
    except Exception as e:
        print(f"[ERR] Import failed: {e}")

if __name__ == "__main__":
    test_environment_setup()
    test_python_path()
    test_launcher_args()
    print("\n=== Verification Completed ===")
