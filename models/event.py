# Class with achievement events as objects

from datetime import date
from typing import List, Dict, Any
from dataclasses import dataclass

from models.person import Person

@dataclass
class Event:
    """Class representing an achievement event."""
    date: date
    person: Person
    achievement: str
    
    @property
    def age_at_event(self) -> int:
        """Return age of the person at the time of the event."""
        return self.person.age_on(self.date)

    def to_dict(self):
        return {
            "year": self.date.year,
            "month": self.date.month,
            "day": self.date.day,
            "text": self.description,
            "name": self.person.name,
            "age": self.age_at_event
        }
    
    
