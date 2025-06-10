# Class with achievement events as objects

from datetime import date
from typing import List, Dict, Any

class Achievement:
    """Class representing an achievement event."""
    def __init__(self, 
                 event_year: int, 
                 event_month: int, 
                 event_day: int, 
                 name: str, 
                 birth_year: int,
                 birth_month: int,
                 birth_day: int,
                 text: str):
        
        self.event_date = date(event_year, event_month, event_day) # Date of the achievement event
        self.name = name # Name of the person who achieved the event
        self.birth_date = date(birth_year, birth_month, birth_day) # Birth date of the person
        self.age = (self.event_date - self.birth_date).days # Age in days at the time of the achievement event
        self.text = text # Description of the achievement event

    

    def __str__(self) -> str:
        return f"{self.name} ({self.year}-{self.month:02d}-{self.day:02d}): {self.text}"