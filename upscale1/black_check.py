# black_check.py
import time
from PIL import Image

def is_black_anomaly(source_path, output_path):
    """
    Checks if the output image is completely black while the source image is NOT.
    Returns True if this anomaly is detected, False otherwise.
    """
    if not output_path.exists() or not source_path.exists():
        return False

    try:
        with Image.open(output_path) as out_img:
            # getbbox() returns None if the image has only zero values (completely black)
            out_bbox = out_img.convert("RGB").getbbox()

        # If the output is completely black, check the source
        if out_bbox is None: 
            with Image.open(source_path) as src_img:
                src_bbox = src_img.convert("RGB").getbbox()
            
            # If the source is NOT completely black, we found an anomaly
            if src_bbox is not None: 
                return True
                
    except Exception as e:
        print(f"⚠️ Error checking black pixels: {e}")
    
    return False
