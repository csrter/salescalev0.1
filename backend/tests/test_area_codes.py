from app.services import area_codes as ac
def test_area_code_state():
    assert ac.state_for_phone("+16022667505") == "AZ"
    assert ac.state_for_phone("+19193822507") == "NC"
    assert ac.state_for_phone("+16152790088") == "TN"
    assert ac.state_for_phone("+14042960050") == "GA"
    assert ac.state_for_phone("+12012362226") == "NJ"
    assert ac.state_for_phone("6022667505") == "AZ"       # no country code
    assert ac.state_for_phone("(602) 266-7505") == "AZ"   # formatted
    assert ac.state_for_phone("+18005551234") is None     # toll-free
    assert ac.state_for_phone("+447911123456") is None    # non-US
    assert ac.state_for_phone(None) is None
    assert ac.state_for_phone("") is None
