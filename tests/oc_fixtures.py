"""Responses shaped like the OpenCorporates v0.4 API documentation examples."""


def company(name, number, jur="gb", status="Active", inc="2001-05-01", inactive=False, officers=None):
    c = {"name": name, "company_number": number, "jurisdiction_code": jur, "company_type": "Private Limited Company", "current_status": status,
         "incorporation_date": inc, "dissolution_date": None, "inactive": inactive, "registered_address_in_full": "1 HIGH STREET, LONDON, SW1Y 4PD",
         "opencorporates_url": f"https://opencorporates.com/companies/{jur}/{number}", "registry_url": f"http://register.example/{number}",
         "previous_names": [{"company_name": name + " OLD LTD", "con_date": "2000-01-01"}],
         "industry_codes": [{"industry_code": {"code": "62.01", "description": "Computer programming activities", "code_scheme_id": "uk_sic_2007"}}],
         "source": {"publisher": "UK Companies House", "url": "http://xmlgw.companieshouse.gov.uk/"}}
    if officers is not None: c["officers"] = officers
    return c


def search(*companies, page=1, total_pages=1, total_count=None):
    return {"api_version": "0.4", "results": {"companies": [{"company": c} for c in companies], "page": page, "per_page": 30,
                                               "total_count": total_count if total_count is not None else len(companies), "total_pages": total_pages}}


def detail(c):
    return {"api_version": "0.4", "results": {"company": c}}


OFFICERS = [
    {"officer": {"id": 1, "name": "SMITH, JANE", "position": "director", "start_date": "2010-02-01", "end_date": None, "opencorporates_url": "https://opencorporates.com/officers/1"}},
    {"officer": {"id": 2, "name": "JOHN OLD", "position": "secretary", "start_date": "2001-11-01", "end_date": "2012-03-30", "opencorporates_url": "https://opencorporates.com/officers/2"}},
]
