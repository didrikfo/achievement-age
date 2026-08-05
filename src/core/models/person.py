from datetime import date
from dataclasses import dataclass

@dataclass
class Person:
    name: str
    birth_date: date
    description: str
    occupation: str
    industry: str
    domain: str

    def age_on(self, event_date: date) -> int:
        """Return the person's age in days at a given event date."""
        return (event_date - self.birth_date).days
