#!/usr/bin/env python3
"""
Readability Scoring using textstat
Calculates reading grade level for web page content
"""

try:
    import textstat
    TEXTSTAT_AVAILABLE = True
except ImportError:
    TEXTSTAT_AVAILABLE = False
    print("textstat not installed. Run: pip install textstat")


def calculate_readability(text: str) -> dict:
    """
    Calculate readability metrics for text content.
    
    Args:
        text: The text content to analyze (should be plain text, not HTML)
    
    Returns:
        Dict with readability scores
    """
    if not TEXTSTAT_AVAILABLE:
        return {"error": "textstat not installed", "grade": None}
    
    if not text or len(text.strip()) < 50:
        return {"error": "insufficient text", "grade": None}
    
    # Clean text
    clean_text = text.strip()
    
    try:
        # Count words and sentences for derived metrics
        word_count = textstat.lexicon_count(clean_text, removepunct=True)
        sentence_count = textstat.sentence_count(clean_text)
        avg_sentence_length = round(word_count / max(1, sentence_count), 1)
        
        # Syllable-based metrics
        syllable_count = textstat.syllable_count(clean_text)
        avg_syllables_per_word = round(syllable_count / max(1, word_count), 2)
        
        # Complex words (3+ syllables)
        difficult_words = textstat.difficult_words(clean_text)
        difficult_words_pct = round((difficult_words / max(1, word_count)) * 100, 1)
        
        # Reading time (average adult reads 200-250 wpm)
        reading_time_mins = round(word_count / 220, 1)
        
        results = {
            # Primary Scores
            "flesch_kincaid_grade": round(textstat.flesch_kincaid_grade(clean_text), 1),
            "flesch_reading_ease": round(textstat.flesch_reading_ease(clean_text), 1),
            "gunning_fog": round(textstat.gunning_fog(clean_text), 1),
            "smog_index": round(textstat.smog_index(clean_text), 1),
            
            # Derived insights
            "avg_sentence_length": avg_sentence_length,
            "avg_syllables_per_word": avg_syllables_per_word,
            "difficult_words_pct": difficult_words_pct,
            "reading_time_mins": reading_time_mins,
            
            # Keep sentence count (useful)
            "sentence_count": sentence_count,
            
            # Summary grade (consensus of multiple formulas)
            "grade": round(textstat.text_standard(clean_text, float_output=True), 1)
        }
        
        # Rating based on grade level
        grade = results["flesch_kincaid_grade"]
        grade_display = int(round(grade))  # Round for display
        
        if grade <= 6:
            results["rating"] = "easy"
            results["rating_label"] = f"Very Easy - Grade {grade_display} level"
        elif grade <= 9:
            results["rating"] = "good"
            results["rating_label"] = f"Good - Grade {grade_display} (general audience)"
        elif grade <= 12:
            results["rating"] = "moderate"
            results["rating_label"] = f"Moderate - Grade {grade_display} level"
        else:
            results["rating"] = "difficult"
            results["rating_label"] = f"Difficult - Grade {grade_display} (college level)"
        
        return results
        
    except Exception as e:
        return {"error": str(e), "grade": None}


def mass_analyze_urls(urls: list) -> list:
    """
    Fetch and analyze readability for multiple URLs.
    
    Args:
        urls: List of URLs to analyze
        
    Returns:
        List of dicts with url + readability scores
    """
    import requests as req
    from html.parser import HTMLParser
    
    class TextExtractor(HTMLParser):
        """Simple HTML to text extractor."""
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self._skip = False
            self._skip_tags = {'script', 'style', 'nav', 'footer', 'header', 'noscript'}
        
        def handle_starttag(self, tag, attrs):
            if tag in self._skip_tags:
                self._skip = True
        
        def handle_endtag(self, tag):
            if tag in self._skip_tags:
                self._skip = False
        
        def handle_data(self, data):
            if not self._skip:
                text = data.strip()
                if text:
                    self.text_parts.append(text)
        
        def get_text(self):
            return ' '.join(self.text_parts)
    
    results = []
    
    for url in urls:
        try:
            resp = req.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            resp.raise_for_status()
            
            extractor = TextExtractor()
            extractor.feed(resp.text)
            text = extractor.get_text()
            
            if len(text) < 50:
                continue
                
            scores = calculate_readability(text)
            if scores.get('error'):
                continue
                
            scores['url'] = url
            results.append(scores)
            
        except Exception as e:
            print(f"Readability fetch failed for {url}: {e}")
            continue
    
    return results


if __name__ == "__main__":
    # Test with sample content
    sample_text = """
    In today's hospitality landscape, comfort is king—and nowhere is this more 
    evident than in the bathroom. For discerning travellers in the UAE's luxury hotels, 
    bathroom amenities in hotel settings are a silent ambassador of brand excellence. 
    From plush bathrobes to bespoke toiletries, these thoughtful touches raise guest 
    satisfaction and differentiate properties in a competitive market.
    
    When guests step into a suite bathroom, the layout and amenities signal the hotel's 
    attention to detail. A well-considered luxury hotel amenities list includes not only 
    the basics—like soap and shampoo—but also indulgences: organic body wash, 
    handcrafted bath salts, soft bamboo towels, and plush slippers.
    """
    
    result = calculate_readability(sample_text)
    
    print("=== READABILITY ANALYSIS ===")
    for key, val in result.items():
        print(f"  {key}: {val}")
