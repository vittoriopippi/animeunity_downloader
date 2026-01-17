from django import forms

class AnimeAddForm(forms.Form):
    url = forms.URLField(label='AnimeUnity URL (or mock URL)', required=True, widget=forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://animeunity.to/anime/...'}))
