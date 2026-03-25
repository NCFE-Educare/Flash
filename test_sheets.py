import asyncio
import sys

from sheets_tools import (
    _validate_a1_notation,
    _validate_full_a1_notation,
    _validate_required,
    _a1_to_grid_range,
    _parse_a1_to_source_range
)

def test_validation():
    print("Testing _validate_a1_notation...")
    # Valid
    _validate_a1_notation("A1")
    _validate_a1_notation("A1:B10")
    
    # Invalid
    try:
        _validate_a1_notation("Sheet1!A1:B10")
        print("ERROR: Sheet1!A1:B10 should have failed")
    except ValueError:
        pass
        
    try:
        _validate_a1_notation("1A")
        print("ERROR: 1A should have failed")
    except ValueError:
        pass
        
    print("Testing _validate_full_a1_notation...")
    # Valid
    _validate_full_a1_notation("Sheet1!A1:B10")
    _validate_full_a1_notation("'My Sheet'!A1")
    
    # Invalid
    try:
        _validate_full_a1_notation("A1:B10")
        print("ERROR: A1:B10 should have failed full a1 notation")
    except ValueError:
        pass

    print("Testing _validate_required...")
    args = {"user_id": 1, "spreadsheet_id": "xyz"}
    _validate_required(args, "user_id", "spreadsheet_id")
    try:
        _validate_required(args, "user_id", "spreadsheet_id", "sheet_name")
        print("ERROR: missing sheet_name should have failed")
    except ValueError:
        pass

    print("Testing _a1_to_grid_range...")
    grid = _a1_to_grid_range(123, "A1:B2")
    assert grid["sheetId"] == 123
    assert grid["startRowIndex"] == 0
    assert grid["endRowIndex"] == 2
    assert grid["startColumnIndex"] == 0
    assert grid["endColumnIndex"] == 2
    
    grid = _a1_to_grid_range(123, "C5")
    assert grid["startRowIndex"] == 4
    assert grid["endRowIndex"] == 5
    assert grid["startColumnIndex"] == 2
    assert grid["endColumnIndex"] == 3

    print("All tests passed!")

if __name__ == "__main__":
    test_validation()
