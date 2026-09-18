import re
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.utils.text import slugify
from .models import UserProfile

class SpotifySocialAdapter(DefaultSocialAccountAdapter):
    
    def save_user(self, request, sociallogin, form=None):
        """
        This method is called when a user logs in via Spotify for the first time.
        It saves the base User and then generates our custom UserProfile.
        """
        # 1. Save the default user instance first
        user = super().save_user(request, sociallogin, form)
        
        # 2. Extract raw data provided by the Spotify API
        extra_data = sociallogin.account.extra_data
        spotify_id = extra_data.get('id')
        display_name = extra_data.get('display_name', 'dancefloor_user')
        
        # Extract profile image if available
        images = extra_data.get('images', [])
        profile_image = images[0].get('url') if images else None

        # 3. Clean and generate the custom Instagram-style handle
        # Turn "Dancefloor Sam!" into "dancefloor_sam"
        base_handle = slugify(display_name).replace('-', '_')
        if not base_handle:
            base_handle = "user"
            
        # 4. Fallback Loop: If 'dancefloor_sam' exists, try 'dancefloor_sam_1', 'dancefloor_sam_2'
        unique_handle = base_handle
        counter = 1
        while UserProfile.objects.filter(app_handle=unique_handle).exists():
            unique_handle = f"{base_handle}_{counter}"
            counter += 1

        # 5. Save the data to our custom UserProfile table
        UserProfile.objects.create(
            user=user,
            spotify_id=spotify_id,
            display_name=display_name,
            profile_image_url=profile_image,
            app_handle=unique_handle
        )
        
        return user