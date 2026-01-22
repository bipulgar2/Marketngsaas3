#!/usr/bin/env python3
"""
Website Screenshot Capture using Playwright
High-quality, reliable screenshot capture for any website
"""
import os
import base64
import subprocess
from typing import Optional


def capture_website_screenshot(url: str, output_path: str = None, width: int = 1920, height: int = 1080) -> Optional[str]:
    """
    Capture a high-resolution screenshot of a website using Playwright.
    
    Args:
        url: The URL to screenshot (with or without protocol)
        output_path: Optional path to save the image file
        width: Viewport width (default 1920)
        height: Viewport height (default 1080)
    
    Returns:
        Base64 encoded image string, or None on error
    """
    try:
        from playwright.sync_api import sync_playwright
        
        # Ensure URL has protocol
        if not url.startswith('http'):
            url = f"https://{url}"
        
        # --- PLAYWRIGHT SETUP ---
        executable_path = None
        try:
            import shutil
            import glob
            
            # 1. Search PATH for common names
            start_time = time.time()
            for name in ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]:
                path = shutil.which(name)
                if path:
                    print(f"DEBUG: Found system chromium via PATH: {path}", flush=True)
                    executable_path = path
                    break
            
            # 2. If not found, fast search in common Nix/Linux locations
            if not executable_path:
                print("DEBUG: Chromium not in PATH, searching common locations...", flush=True)
                common_paths = [
                    "/usr/bin/chromium", 
                    "/usr/bin/chromium-browser",
                    "/usr/local/bin/chromium",
                    "/nix/var/nix/profiles/default/bin/chromium",
                    "/root/.nix-profile/bin/chromium"
                ]
                for p in common_paths:
                    if os.path.exists(p) and os.access(p, os.X_OK):
                         print(f"DEBUG: Found system chromium at common path: {p}", flush=True)
                         executable_path = p
                         break

            # 3. Slow search (Find command equivalent) - limit depth or specific dir if possible
            if not executable_path and os.path.exists("/nix"):
                 print("DEBUG: Searching /nix store for chromium binary...", flush=True)
                 # This is expensive, but we only do it if all else fails. 
                 # Look for 'chromium' binary in bin directories within nix store
                 # Using find via subprocess is faster than python walk for deep trees
                 try:
                     find_cmd = ["find", "/nix/store", "-name", "chromium", "-type", "f", "-path", "*/bin/chromium"]
                     result = subprocess.run(find_cmd, capture_output=True, text=True, timeout=10) # 10s timeout
                     paths = result.stdout.strip().split('\n')
                     # Filter for executable ones
                     valid_paths = [p for p in paths if p and os.access(p, os.X_OK)]
                     if valid_paths:
                         # Pick the shortest path (likely the main one) or first
                         executable_path = valid_paths[0]
                         print(f"DEBUG: Found chromium specific path in /nix: {executable_path}", flush=True)
                 except Exception as find_err:
                     print(f"DEBUG: Find command failed: {find_err}", flush=True)

            if not executable_path:
                print("DEBUG: System chromium STILL not found, relying on Playwright default", flush=True)
                
        except Exception as e:
            print(f"DEBUG: Error checking system chromium: {e}", flush=True)
        # -----------------------------
        
        try:
            with sync_playwright() as p:
                # Launch config
                launch_args = {
                    "headless": True,
                    "args": ["--no-sandbox", "--disable-setuid-sandbox"] # Essential for container envs
                }
                if executable_path:
                    launch_args["executable_path"] = executable_path

                # Launch browser
                browser = p.chromium.launch(**launch_args)
                print(f"DEBUG: Browser launched successfully (Executable: {executable_path or 'Default'})", flush=True)
                
                # Create context with viewport size
                context = browser.new_context(
                    viewport={'width': width, 'height': height},
                    device_scale_factor=2,  # 2x for high DPI/retina quality
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                
                page = context.new_page()
                page.goto(url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(2000)
                
                screenshot_bytes = page.screenshot(type='png', full_page=False)
                browser.close()
                
                # Save to file if path provided
                if output_path:
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, 'wb') as f:
                        f.write(screenshot_bytes)
                    print(f"DEBUG: Screenshot saved to {output_path}", flush=True)
                
                base64_image = base64.b64encode(screenshot_bytes).decode()
                print(f"DEBUG: Screenshot captured successfully ({len(screenshot_bytes)} bytes)", flush=True)
                return f"data:image/png;base64,{base64_image}"

        except Exception as e:
            print(f"DEBUG: Playwright launch failed: {e}", flush=True)
            raise e

    except ImportError:
        print("ERROR: Playwright not installed.", flush=True)
        return None
    except Exception as e:
        print(f"ERROR capturing screenshot with Playwright: {e}", flush=True)
        # Check if it was because of the re-raised exception
        return None


def capture_screenshot_with_fallback(url: str) -> Optional[str]:
    """
    Capture screenshot using DataForSEO API.
    The image is cropped to 16:9 ratio for proper slide display.
    
    Args:
        url: The URL to screenshot
        
    Returns:
        Base64 encoded image string, or None if capture fails
    """
    print(f"DEBUG: Capturing screenshot for {url} using DataForSEO")
    try:
        from api.dataforseo_client import fetch_dataforseo_screenshot
        
        # Ensure URL has protocol for DataForSEO
        if not url.startswith('http'):
            url = f"https://{url}"
            
        screenshot_b64 = fetch_dataforseo_screenshot(url)
        
        if screenshot_b64:
            # Crop to 16:9 ratio for proper slide display
            cropped_b64 = crop_image_to_16_9(screenshot_b64)
            if cropped_b64:
                screenshot_b64 = cropped_b64
            
            # Add data prefix if not present
            if screenshot_b64.startswith('data:image'):
                return screenshot_b64
            else:
                return f"data:image/png;base64,{screenshot_b64}"
                
    except Exception as e:
        print(f"ERROR DataForSEO screenshot failed: {e}")
    
    print(f"WARNING: Screenshot capture failed for {url}")
    return None


def crop_image_to_16_9(base64_image: str) -> Optional[str]:
    """
    Crop an image to 16:9 aspect ratio from top-center.
    This ensures the screenshot fits properly on the slide layout.
    
    Args:
        base64_image: Base64 encoded image string (with or without data: prefix)
        
    Returns:
        Base64 encoded cropped image, or None if cropping fails
    """
    try:
        from PIL import Image
        from io import BytesIO
        
        # Remove data prefix if present
        if base64_image.startswith('data:'):
            base64_image = base64_image.split(',')[1]
        
        # Decode base64 to image
        image_data = base64.b64decode(base64_image)
        img = Image.open(BytesIO(image_data))
        
        original_width, original_height = img.size
        target_ratio = 16 / 9
        current_ratio = original_width / original_height
        
        print(f"DEBUG: Original image size: {original_width}x{original_height} (ratio: {current_ratio:.2f})", flush=True)
        
        # If already 16:9 (or close), return as-is
        if abs(current_ratio - target_ratio) < 0.1:
            print("DEBUG: Image already close to 16:9, no cropping needed", flush=True)
            return None
        
        # Calculate crop dimensions
        if current_ratio < target_ratio:
            # Image is too tall (narrow), crop height from bottom
            new_width = original_width
            new_height = int(original_width / target_ratio)
            # Crop from top, keeping header/hero area
            crop_box = (0, 0, new_width, new_height)
        else:
            # Image is too wide, crop width from sides
            new_height = original_height
            new_width = int(original_height * target_ratio)
            # Center crop horizontally
            left = (original_width - new_width) // 2
            crop_box = (left, 0, left + new_width, new_height)
        
        # Perform crop
        cropped_img = img.crop(crop_box)
        print(f"DEBUG: Cropped image to {cropped_img.size[0]}x{cropped_img.size[1]}", flush=True)
        
        # Encode back to base64
        buffer = BytesIO()
        cropped_img.save(buffer, format='PNG')
        cropped_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        return cropped_b64
        
    except ImportError:
        print("WARNING: Pillow not installed, skipping image crop", flush=True)
        return None
    except Exception as e:
        print(f"WARNING: Image crop failed: {e}", flush=True)
        return None


if __name__ == "__main__":
    # Test the screenshot capture
    test_url = "https://example.com"
    print(f"\nTesting screenshot capture for: {test_url}")
    
    result = capture_screenshot_with_fallback(test_url)
    
    if result:
        print(f"SUCCESS: Screenshot captured ({len(result)} chars base64)")
    else:
        print("FAILED: Could not capture screenshot")
