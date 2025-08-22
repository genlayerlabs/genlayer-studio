import { WebDriver, By } from 'selenium-webdriver';

import { ValidatorsPage } from '../pages/ValidatorsPage.js';
import { ContractsPage } from '../pages/ContractsPage.js';
import { before, describe, after, it } from 'node:test';
import { expect } from 'chai';
import { getDriver } from '../utils/driver.js';

let driver: WebDriver;
let validatorsPage: ValidatorsPage;
let contractsPage: ContractsPage;

describe('Settings - Delete Node Validator', () => {
  before(async () => {
    driver = await getDriver();
    validatorsPage = new ValidatorsPage(driver);
    contractsPage = new ContractsPage(driver);
  });

  it('should delete an existing validator', async () => {
    await contractsPage.navigate();
    await contractsPage.waitUntilVisible();
    await contractsPage.skipTutorial();

    await validatorsPage.navigate();
    await validatorsPage.waitUntilVisible();

    await validatorsPage.createValidator({
      provider: 'heuristai',
      model: 'mistralai/mixtral-8x7b-instruct',
      stake: 7,
    });
    const existingValidators = await validatorsPage.getValidatorsElements();
    const existingValidatorsLength = existingValidators.length;
    expect(
      existingValidatorsLength,
      'number of validators should be greather than 0',
    ).be.greaterThan(0);

    const firstValidator = existingValidators[0];

    // Trigger hover to display delete btn
    // !!! Tester should not move the mouse during this test
    await driver.actions().move({ origin: firstValidator }).perform();
    await driver.sleep(1000);

    const deleteValidatorBtn = await firstValidator.findElement(
      By.xpath("//button[@data-testid='validator-item-delete']"),
    );

    await deleteValidatorBtn.click();
    await driver.sleep(1000);

    const confirmDeleteValidatorBtn = await firstValidator.findElement(
      By.xpath("//button[@data-testid='validator-item-confirm-delete']"),
    );
    // call delete validator button
    await confirmDeleteValidatorBtn.click();

    await driver.navigate().refresh();
    const validators = await driver.findElements(
      By.xpath("//button[@data-testid = 'validator-item']"),
    );

    expect(
      validators.length,
      'validators length should be less than initial validators length',
    ).be.lessThan(existingValidatorsLength);
  });

  after(() => driver.quit());
});
