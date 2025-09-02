from dotenv import load_dotenv
import os
from google import genai
from google.genai import types
from utils import load_json, save_to_json, parse_llm_output


load_dotenv(dotenv_path='dotenv.env')
api_key = os.getenv('PRIVATE_API_KEY')
client = genai.Client(api_key=api_key)

test_data = '''[
                {
                    "text": "Albert Einstein's paper that leads to the mass-energy equivalence formula, E = mc², is published in the journal Annalen der Physik.",
                    "name": "Albert Einstein"
                },
                {
                    "text": "Henry VIII of England marries his fifth wife, Catherine Howard.",
                    "name": "Henry VIII of England"
                },
                {
                    "text": "American actress Marilyn Monroe is found dead at her home from a drug overdose.",
                    "name": "Marilyn Monroe",
                },
                {   "text": "Amelia Earhart flies her airplane solo for the first time.",
                    "name": "Amelia Earhart"
                },
                {   "text": "Only a few weeks after Black Sabbaths farewell concert, Ozzy Osbourne dies.",
                    "name": "Ozzy Osbourne"
                }

        ]'''

test_data_full = '''  {
    "year": "1928",
    "month": 1,
    "day": 31,
    "text": "Leon Trotsky is exiled to Alma-Ata.",
    "name": "Leon Trotsky",
    "age": 17616
  },
  {
    "year": "1943",
    "month": 1,
    "day": 31,
    "text": "World War II: German field marshal Friedrich Paulus surrenders to the Soviets at Stalingrad, followed two days later by the remainder of his Sixth Army, ending one of the war's fiercest battles.",
    "name": "Friedrich Paulus",
    "age": 19122
  },
  {
    "year": "1865",
    "month": 2,
    "day": 1,
    "text": "President Abraham Lincoln signs the Thirteenth Amendment to the United States Constitution.",
    "name": "Abraham Lincoln",
    "age": 20443
  },
  {
    "year": "1942",
    "month": 2,
    "day": 1,
    "text": "World War II: Josef Terboven, Reichskommissar of German-occupied Norway, appoints Vidkun Quisling the Minister President of the National Government.",
    "name": "Vidkun Quisling",
    "age": 19921
  },'''

def test_batch_job(client = client):
    # A list of dictionaries, where each is a GenerateContentRequest
    inline_requests = [
        {
            'contents': [{
                'parts': [{'text': 'Tell me a one-sentence joke.'}],
                'role': 'user'
            }]
        },
        {
            'contents': [{
                'parts': [{'text': 'Why is the sky blue?'}],
                'role': 'user'
            }]
        }
    ]

    inline_batch_job = client.batches.create(
        model="models/gemini-2.0-flash",
        src=inline_requests,
        config={
            'display_name': "inlined-requests-job-1",
        },
    )

    print(f"Created batch job: {inline_batch_job.name}")


def reword_event_descriptions(client = client, data: list[dict] = test_data_full):
    instructions = '''For each element in the following list of dicts, find the event that is described in the `text` field. Return the same list of dicts, with a new field added to each dict. 
                    The new field should be called "display_text" and should contain the string "The same age that {name} was when {event happened}", 
                    where {name} is the name given in the dict, {he/she/they} is chosen as apropriate based on the name and text (use they if you are unsure), 
                    and {event happened} is the event described in the "text" field, phrased in a fitting way gramatically. 
                    Return the results formatted as a list of dicts, containing all the dicts that appear in the input data, each of which having the new "display_tex" field added. 
                    The output should contain only a list of dicts. Under no circumstances should the output contain code or comments. 
                    Do not include any text explaining the output or your reasoning, only the actual output list of tuples.'''

    all_data = load_json(os.path.join("data","events_with_age.json"))
    data = all_data[:100]

    content = instructions + str(data)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=content,
    )

    parsed_llm_output = parse_llm_output(response.text)

    save_to_json(os.path.join('data','displayable_events.json'), parsed_llm_output)

    return parsed_llm_output

