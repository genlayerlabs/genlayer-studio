import { WebDriver } from 'selenium-webdriver';

import { ValidatorsPage } from '../pages/ValidatorsPage.js';
import { ContractsPage } from '../pages/ContractsPage.js';
import { before, describe, after, it } from 'node:test';
import { expect } from 'chai';
import { getDriver } from '../utils/driver.js';

let driver: WebDriver;
let validatorsPage: ValidatorsPage;
let contractsPage: ContractsPage;

describe('Settings - Create Node Validator', () => {
  before(async () => {
    driver = await getDriver();
    validatorsPage = new ValidatorsPage(driver);
    contractsPage = new ContractsPage(driver);
  });

  it('should create a new validator', async () => {
    await contractsPage.navigate();
    await contractsPage.waitUntilVisible();
    await contractsPage.skipTutorial();

    await validatorsPage.navigate();
    await validatorsPage.waitUntilVisible();

    const initialValidators = await validatorsPage.getValidatorsElements();
    await validatorsPage.createValidator({
      provider: 'heuristai',
      model: 'mistralai/mixtral-8x7b-instruct',
      stake: 7,
    });
    const existingValidators = await validatorsPage.getValidatorsElements();
    expect(
      existingValidators.length,
      'number of validators should be greather than old validators list',
    ).be.greaterThan(initialValidators.length);
  });

  after(() => driver.quit());
});
