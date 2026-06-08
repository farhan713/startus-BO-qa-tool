Feature: Customer Management — full workflow
  As a back-office user
  I want to manage customers (search, create, edit, print, delete)
  So that customer data stays current and accessible

  Background:
    Given the user is logged in to Stratus BackOffice
    And the user is on the Customer List screen

  @demo @smoke
  Scenario: Customer List loads with all expected actions
    Then the page shows the New button
    And the page shows the Action dropdown
    And the page shows the Close button
    And the page shows Show/Hide Search Criteria controls

  @demo
  Scenario: Searching customers by last name
    When the user opens the search criteria
    And the user searches for last name "SMITH"
    Then the grid finishes loading
    And the search criteria can be reset

  @demo
  Scenario: Create a new customer
    When the user clicks New
    Then the Customer Detail page opens
    When the user fills in first name "QA-Demo" last name "TesterBDD" company "StratusQ"
    And the user clicks Save
    Then the Customer Detail page shows no errors

  @demo
  Scenario: Edit an existing customer from the grid
    When the user opens the search criteria
    And the user searches for last name "SMITH"
    And the user selects the first row
    And the user clicks the Edit action
    Then the Customer Detail page opens
    When the user changes first name to "UpdatedBDD"
    And the user clicks Save
    Then the Customer Detail page shows no errors

  @demo
  Scenario: Print the customer list
    When the user opens the search criteria
    And the user searches for last name "SMITH"
    And the user clicks the Print List action
    Then a print job is initiated

  @demo
  Scenario: Close the Customer List screen
    When the user clicks Close
    Then the user is no longer on the Customer List screen
