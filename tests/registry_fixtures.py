"""GLEIF responses trimmed from real api.gleif.org replies (2026-09-29); Companies House shaped per its API spec."""


def lei(lei, name, number, country="GB", jurisdiction="GB", status="ACTIVE", created="1996-06-20T00:00:00Z", other_names=(), city="Birmingham"):
    return {"type": "lei-records", "id": lei, "attributes": {"lei": lei, "entity": {
        "legalName": {"name": name, "language": "en"},
        "otherNames": [{"name": n, "language": "en", "type": t} for n, t in other_names],
        "legalAddress": {"language": "en", "addressLines": ["The Mailbox Level 3, 101 Wharfside Street"], "city": city, "region": "GB-BIR", "country": country, "postalCode": "B1 1RF"},
        "headquartersAddress": {"language": "en", "addressLines": ["The Mailbox Level 3, 101 Wharfside Street"], "city": city, "country": country, "postalCode": "B1 1RF"},
        "registeredAt": {"id": "RA000585", "other": None}, "registeredAs": number, "jurisdiction": jurisdiction, "category": "GENERAL",
        "legalForm": {"id": "H0PO", "other": None}, "status": status, "expiration": {"date": None, "reason": None}, "creationDate": created},
        "registration": {"initialRegistrationDate": "2014-06-05T00:00:00Z", "lastUpdateDate": "2026-01-15T09:59:26Z", "status": "ISSUED"},
        "ocid": f"gb/{number}"}, "links": {"self": f"https://api.gleif.org/api/v1/lei-records/{lei}"}}


def lei_page(*records, page=1, last=1, total=None):
    return {"meta": {"pagination": {"currentPage": page, "perPage": 30, "from": 1, "to": len(records), "total": total if total is not None else len(records), "lastPage": last}},
            "data": list(records)}


ONEADVANCED = lei("213800SJAISA1Z1RM538", "ONEADVANCED LIMITED", "03214465", other_names=[("ADVANCED BUSINESS SOFTWARE AND SOLUTIONS LIMITED", "PREVIOUS_LEGAL_NAME")])
ARBISOFT = lei("984500BA7015E104BW35", "Arbisoft Incorporated", "2059764", country="VG", jurisdiction="VG", created="2021-04-12T00:00:00Z", city="Road Town")


def ch_item(name, number, status="active", created="2009-04-02"):
    return {"company_name": name, "title": name, "company_number": number, "company_status": status, "company_type": "ltd", "date_of_creation": created,
            "registered_office_address": {"address_line_1": "1 High Street", "locality": "London", "postal_code": "SW1Y 4PD", "country": "United Kingdom"},
            "address": {"address_line_1": "1 High Street", "locality": "London", "postal_code": "SW1Y 4PD"}, "sic_codes": ["62012"], "kind": "searchresults#company"}


def ch_profile(name, number):
    return {"company_name": name, "company_number": number, "company_status": "active", "type": "ltd", "date_of_creation": "2009-04-02",
            "registered_office_address": {"address_line_1": "1 High Street", "locality": "London", "postal_code": "SW1Y 4PD"}, "sic_codes": ["62012"],
            "previous_company_names": [{"name": "ACME SOFT LTD", "ceased_on": "2012-01-01"}]}


CH_OFFICERS = {"items": [
    {"name": "SMITH, Jane", "officer_role": "director", "appointed_on": "2010-02-01", "occupation": "Engineer", "links": {"officer": {"appointments": "/officers/abc/appointments"}}},
    {"name": "OLD, John", "officer_role": "secretary", "appointed_on": "2001-11-01", "resigned_on": "2012-03-30", "links": {"officer": {"appointments": "/officers/def/appointments"}}}],
    "active_count": 1, "resigned_count": 1, "total_results": 2}
