from datetime import date

from utils import load_json

def get_user_age_in_days():
    """Prompt the user for their birthday and calculate their age in days."""
    while True:
        try:
            year = int(input("Enter your birth year (YYYY): "))
            month = int(input("Enter your birth month (1-12): "))
            day = int(input("Enter your birth day (1-31): "))
            # Validate the date
            birthday = date(year, month, day)
            today = date.today()
            age_in_days = (today - birthday).days
            return age_in_days, birthday
        except ValueError:
            print("Invalid input. Please enter numeric values for year, month, and day.")

def search_for_event_matching_age(events, user_age):
    """Search for events where the age in days matches the user's age."""
    matching_events = []
    for event in events:
        if "age" in event and event["age"] == user_age:
            matching_events.append(event)
    return matching_events

def display_matching_events(matching_events, birthday: date):
    """Display the users age and matching events."""
    # Display the user's age in years and days
    user_age_years = date.today().year - birthday.year
    birthday_this_year = date(date.today().year, birthday.month, birthday.day)
    if date.today().month < birthday.month or (date.today().month == birthday.month and date.today().day < birthday.day):
        user_age_years -= 1
        birthday_this_year = date(date.today().year - 1, birthday.month, birthday.day)
    # Calculate remaining days after full years
    user_age_days = (date.today() - birthday_this_year).days
    print(f"\nYour age is {user_age_years} years and {user_age_days} days old.")

    if not matching_events:
        print("No events found matching your age.")
        return

    for event in matching_events:
        print(f"When {event['name']} was that age:")
        print(event['text'])


def main():
    # Load events data
    events = load_json("data/events_with_age.json")

    # Get user's age in days
    user_age, birthday = get_user_age_in_days()

    # Search for events matching the user's age
    matching_events = search_for_event_matching_age(events, user_age)

    # Display the matching events
    display_matching_events(matching_events, birthday)

if __name__ == "__main__":
    main()
