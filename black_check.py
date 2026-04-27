# black_check.py
import time
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np
from scipy.ndimage import label, find_objects
import shutil

def is_black_anomaly(source_path, output_path, min_size=(50, 50)):
    """
    Checks if output has a black region larger than min_size (width, height)
    that was NOT dark in the source image.
    Returns: (boolean indicating if anomaly exists, list of bounding box coordinates)
    """
    if not output_path.exists() or not source_path.exists():
        return False, []

    large_boxes = []
    try:
        with Image.open(output_path) as img:
            arr_out = np.array(img.convert("L"))
            # Find very black pixels in the upscaled image (value < 5)
            mask_out = (arr_out < 5)
            
            with Image.open(source_path) as src:
                # Resize source to match upscaled dimensions for accurate comparison
                if src.size != img.size:
                    # FIX for Ubuntu 22.04: Fallback to Image.BILINEAR if Image.Resampling doesn't exist
                    resample_method = getattr(Image, 'Resampling', Image).BILINEAR
                    src = src.resize(img.size, resample_method)
                
                arr_src = np.array(src.convert("L"))
                # Find dark pixels in the source. Threshold < 20 ignores existing shadows
                mask_src = (arr_src < 20)
                
            # Anomaly is True ONLY if it's black in output AND it was NOT dark in the source
            anomaly_mask = mask_out & (~mask_src)
            
            # Label contiguous regions of actual anomalies
            labeled_array, num_features = label(anomaly_mask.astype(int))
            
            if num_features > 0:
                slices = find_objects(labeled_array)
                for sl in slices:
                    if sl is None: 
                        continue
                    
                    # Extract bounding box coordinates
                    y_slice, x_slice = sl
                    y0, y1 = y_slice.start, y_slice.stop
                    x0, x1 = x_slice.start, x_slice.stop
                    
                    height = y1 - y0
                    width = x1 - x0
                    
                    # If region meets minimum size, save its coordinates
                    if width >= min_size[0] and height >= min_size[1]:
                        large_boxes.append((x0, y0, x1, y1))
                        
        if large_boxes:
            return True, large_boxes
                
    except Exception as e:
        print(f"⚠️ Error processing {output_path.name}: {e}")
    
    return False, []

def generate_black_diagnostics(source_path, output_path, debug_folder, min_size=(50, 50)):
    source_path = Path(source_path)
    output_path = Path(output_path)
    debug_folder = Path(debug_folder)
    
    possible_paths = [
        output_path,                                     
        debug_folder / f"output_{output_path.name}",     
        debug_folder / f"moved_{output_path.name}",      
        debug_folder / output_path.name                  
    ]
    
    resolved_path = None
    for path in possible_paths:
        if path.exists():
            resolved_path = path
            break
            
    if resolved_path is None:
        print(f"⚠️ Diagnostics failed: Could not find {output_path.name} anywhere.")
        return {"error": "File not found"}
        
    is_anomaly, boxes = is_black_anomaly(source_path, resolved_path, min_size)
    
    diag_info = {
        "anomaly_detected": is_anomaly,
        "boxes": boxes
    }
    
    if is_anomaly:
        try:
            debug_folder.mkdir(parents=True, exist_ok=True)
            
            with Image.open(resolved_path) as img:
                img_rgb = img.convert("RGB")
                draw = ImageDraw.Draw(img_rgb)
                
                for box in boxes:
                    draw.rectangle(box, outline="red", width=3)
                
                clean_name = resolved_path.name.replace("output_", "").replace("moved_", "")
                save_path = debug_folder / f"highlighted_{clean_name}"
                
                img_rgb.save(save_path)
                
                diag_info["highlighted_file"] = str(save_path)
                print(f"  -> 💾 Saved highlighted image to: {save_path.name}")
                
        except Exception as e:
            print(f"⚠️ Error drawing diagnostics: {e}")
            diag_info["error"] = str(e)
            
    return diag_info
