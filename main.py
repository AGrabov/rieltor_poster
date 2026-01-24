from models.rieltor_dataclasses import Offer
from dotenv import load_dotenv
load_dotenv()

current_offer: Offer = Offer()

def get_data_from_crm():
    pass

def create_offer(dict_offer: dict) -> Offer:
    pass

def create_offer_draft(offer: Offer) -> None:
    pass

def publish_offer() -> None:
    pass