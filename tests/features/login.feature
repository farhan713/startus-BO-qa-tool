Feature: Stratus BackOffice login
  As a back-office user
  I want to authenticate against the application
  So that I can access POS, intake, and reporting screens

  Background:
    Given the user is on the Stratus BackOffice login page

  @smoke @ui @bdd
  Scenario: Valid credentials grant access
    When the user submits valid credentials
    Then the user is taken away from the login page

  @smoke @ui @bdd
  Scenario: Invalid credentials are rejected
    When the user submits invalid credentials
    Then the user remains on the login page or sees an error message
