from scraper.extract import fix_mojibake, people_in


def test_real_people_still_found():
    assert people_in("Imran Aslam\nChief Executive Officer") == [("Imran Aslam", "Chief Executive Officer")]
    assert people_in("Mr. Fareed Mughis Sheikh\nDirector/Chief Executive Officer") == [("Mr. Fareed Mughis Sheikh", "Director/Chief Executive Officer")]
    assert people_in("Bilal\nMahmood\nManaging Director") == [("Bilal Mahmood", "Managing Director")]
    assert people_in("Sara Khan - Founder") == [("Sara Khan", "Founder")]
    assert people_in("Naeem Ullah Cheema\n� CEO.") == [("Naeem Ullah Cheema", "CEO")]


def test_headings_are_not_people():
    assert people_in("Apply Now\nExclusive Podcast with our CEO") == []
    assert people_in("Birthday Celebrations\n“If I Were a CEO” Activity") == []
    assert people_in("Download Company Profile\nCEO's Message") == []
    assert people_in("Download\nProfile\nCEO") == []
    assert people_in("Company About\nCEO") == []
    assert people_in("History Vision\nChairman") == []
    assert people_in("Happy Birthday\nCEO") == []
    assert people_in("Christmas Day\n“If I Were a CEO” Activity") == []


def test_fix_mojibake():
    assert fix_mojibake("â€œIf I Were a CEOâ€\x9d Activity") == "“If I Were a CEO” Activity"
    assert fix_mojibake("â€œIf I Were a CEOâ€\udc9d Activity") == "“If I Were a CEO” Activity"
    assert fix_mojibake("CafÃ© Ltd") == "Café Ltd"
    assert fix_mojibake("Café “ok”") == "Café “ok”"
    assert fix_mojibake("") == ""
