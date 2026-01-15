import { WebDriver, By, until } from 'selenium-webdriver';
import { ValidatorsPage } from '../pages/ValidatorsPage.js';
import { ContractsPage } from '../pages/ContractsPage.js';
import { before, describe, after, it } from 'node:test';
import { expect } from 'chai';
import { getDriver } from '../utils/driver.js';

let driver: WebDriver;
let validatorsPage: ValidatorsPage;
let contractsPage: ContractsPage;

describe('Settings - Update Node Validator', () => {
  before(async () => {
    driver = await getDriver();
    validatorsPage = new ValidatorsPage(driver);
    contractsPage = new ContractsPage(driver);
  });

  it('should update an existing validator', async () => {
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
    expect(
      existingValidators.length,
      'number of validators should be greather than 0',
    ).be.greaterThan(0);

    const validator = await existingValidators[0].findElement(
      By.xpath("//div[@data-testid = 'validator-item']"),
    );
    await validator.click();

    const stakeInput = await driver.wait(
      until.elementLocated(By.xpath("//input[@data-testid='input-stake']")),
      2000,
    );
    await stakeInput.clear();
    await stakeInput.sendKeys(8);

    const updateValidatorBtn = await driver.wait(
      until.elementLocated(
        By.xpath("//button[@data-testid='btn-update-validator']"),
      ),
      2000,
    );
    // call save validator button
    await updateValidatorBtn.click();

    driver.sleep(2000);

    await validator.click();

    const stakeInputNew = await driver.wait(
      until.elementLocated(By.xpath("//input[@data-testid='input-stake']")),
      2000,
    );
    const stake = await stakeInputNew.getAttribute('value');
    expect(stake, 'stake should be 8').be.equal('8');
  });

  after(() => driver.quit());
});
