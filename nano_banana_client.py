import os
import time
import uuid
import gemini_client

class NanoBananaClient:
    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public', 'generated_images')
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            
        # Initialize Supabase
        url: str = os.environ.get("SUPABASE_URL")
        key: str = os.environ.get("SUPABASE_KEY")
        if url and key:
            from supabase import create_client
            self.supabase = create_client(url, key)
        else:
            self.supabase = None
            print("WARNING: Supabase credentials missing in NanoBananaClient")

    def generate_image(self, prompt, aspect_ratio="16:9"):
        """
        Generates an image using Gemini (via gemini_client).
        Saves it locally AND uploads to Supabase Storage.
        Returns the PUBLIC URL.
        """
        print(f"NanoBananaClient (Gemini): Generating image for prompt: {prompt}")
        
        filename = f"{uuid.uuid4()}.jpg"
        output_path = os.path.join(self.output_dir, filename)
        
        # Use gemini_client to generate and save
        result_path = gemini_client.generate_image(
            prompt=prompt, 
            output_path=output_path,
            aspect_ratio=aspect_ratio
        )
        
        if result_path:
            # 1. Upload to Supabase if configured
            if self.supabase:
                try:
                    with open(result_path, 'rb') as f:
                        self.supabase.storage.from_('photoshoots').upload(
                            path=filename,
                            file=f,
                            file_options={"content-type": "image/jpeg"}
                        )
                    # Get Public URL
                    public_url = self.supabase.storage.from_('photoshoots').get_public_url(filename)
                    print(f"Image uploaded to Supabase: {public_url}")
                    return public_url
                except Exception as e:
                    print(f"Error uploading to Supabase: {e}")
                    # Fallback to local URL if upload fails
                    return f"/generated-images/{filename}"
            else:
                return f"/generated-images/{filename}"
        else:
            raise Exception("Failed to generate image with Gemini")

nano_banana_client = NanoBananaClient()
