import { WebDriver, By, until } from 'selenium-webdriver';
import { ContractsPage } from '../pages/ContractsPage.js';
import { RunDebugPage } from '../pages/RunDebugPage.js';
import { ValidatorsPage } from '../pages/ValidatorsPage.js';
import { before, describe, after, it } from 'node:test';
import { expect } from 'chai';
import { getDriver } from '../utils/driver.js';

let driver: WebDriver;
let contractsPage: ContractsPage;
let runDebugPage: RunDebugPage;
let validatorsPage: ValidatorsPage;

describe('Contract Example WizardOfCoin', () => {
  before(async () => {
    driver = await getDriver();
    contractsPage = new ContractsPage(driver);
    runDebugPage = new RunDebugPage(driver);
    validatorsPage = new ValidatorsPage(driver);

    await contractsPage.navigate();
    await contractsPage.waitUntilVisible();
    await contractsPage.skipTutorial();
    await validatorsPage.navigate();
    await validatorsPage.createValidatorIfRequired();
  });

  it('should open WizardOfCoin example contract', async () => {
    await contractsPage.navigate();
    await contractsPage.waitUntilVisible();
    await contractsPage.openContract('wizard_of_coin.py');
    const tabs = await driver.findElements(
      By.xpath("//div[contains(@class, 'contract-item')]"),
    );
    expect(tabs.length, 'Number of tabs should be 1').equal(1);
  });

  it('should open Run debug page and set constructor arguments for WizardOfCoin', async () => {
    await runDebugPage.navigate();
    await runDebugPage.waitUntilVisible();

    const nameOfContract = await driver.wait(
      until.elementLocated(
        By.xpath("//div[contains(text(), 'wizard_of_coin.py')]"),
      ),
      2000,
    );
    expect(nameOfContract, 'WizardOfCoin file name contract should be visible')
      .not.null;

    const haveCoinCheck = await driver.wait(
      until.elementLocated(
        By.xpath(
          "//input[contains(@name, 'have_coin') and contains(@type, 'checkbox')]",
        ),
      ),
      2000,
    );
    expect(haveCoinCheck, 'Have coin checkbox should be visible').not.null;
    await haveCoinCheck.click();
  });

  it('should deploy the contract WizardOfCoin', async () => {
    await driver
      .wait(
        until.elementLocated(
          By.xpath("//button[@data-testid='btn-deploy-contract']"),
        ),
        2000,
      )
      .click();

    // locate elements that should be visible
    const deployedContractInfo = await driver.wait(
      until.elementLocated(
        By.xpath("//*[@data-testid='deployed-contract-info']"),
      ),
      20000,
    );

    expect(deployedContractInfo, 'Deployed contract info should be visible').not
      .null;

    const readMethods = await driver.findElement(
      By.xpath("//*[@data-testid='contract-read-methods']"),
    );

    expect(readMethods, 'Read methods should be visible').not.null;

    const latestTransactions = await driver.findElement(
      By.xpath("//*[@data-testid='latest-transactions']"),
    );

    expect(latestTransactions, 'Latest transactions section should be visible')
      .not.null;
  });

  it('should call get_have_coin state', async () => {
    const methodBtn = await driver.findElement(
      By.xpath("//button[@data-testid='expand-method-btn-get_have_coin']"),
    );
    await methodBtn.click();

    const readBtn = await driver.findElement(
      By.xpath("//button[@data-testid='read-method-btn-get_have_coin']"),
    );

    await readBtn.click();

    const methodResponse = await driver.findElement(
      By.xpath("//*[@data-testid='method-response-get_have_coin']"),
    );

    expect(methodResponse, 'get_have_coin result should be visible').not.null;

    const methodResponseText = await driver
      .wait(until.elementTextContains(methodResponse, 'true'), 5000)
      .getText();

    expect(
      methodResponseText.includes('true'),
      'get_have_coin result should contain "true"',
    ).to.be.true;
  });

  it('should call ask_for_coin() method', async () => {
    const methodBtn = await driver.findElement(
      By.xpath("//button[@data-testid='expand-method-btn-ask_for_coin']"),
    );
    await methodBtn.click();

    const requestInput = await driver.findElement(
      By.xpath("//input[@name='request']"),
    );
    await requestInput.clear();
    await requestInput.sendKeys('Please give me the coin');
    const requestText = await requestInput.getAttribute('value');
    expect(
      requestText,
      'The input text should be equal to `Please give me the coin`',
    ).to.be.equal('Please give me the coin');

    const writeBtn = await driver.findElement(
      By.xpath("//button[@data-testid='write-method-btn-ask_for_coin']"),
    );

    await writeBtn.click();
  });
  after(() => driver.quit());
});
