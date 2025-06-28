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

describe('Contract Example Storage', () => {
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

  it('should open Storage example contract', async () => {
    await contractsPage.navigate();
    await contractsPage.waitUntilVisible();
    await contractsPage.openContract('storage.py');
    const tabs = await driver.findElements(
      By.xpath("//div[contains(@class, 'contract-item')]"),
    );
    expect(tabs.length, 'Number of tabs should be 1').equal(1);
  });

  it('should open Run debug page and set constructor arguments for Storage', async () => {
    await runDebugPage.navigate();
    await runDebugPage.waitUntilVisible();

    const nameOfContract = await driver.findElement(
      By.xpath(
        "//*[@data-testid='current-contract-name' and contains(text(), 'storage.py')]",
      ),
    );

    expect(nameOfContract, 'Storage file name contract should be visible').not
      .null;

    const initialStorageInput = await driver.findElement(
      By.xpath(
        "//input[contains(@name, 'initial_storage') and contains(@type, 'text')]",
      ),
    );

    expect(initialStorageInput, 'Initial Storage input should be visible').not
      .null;
    await initialStorageInput.clear();
    await initialStorageInput.sendKeys('Test initial storage');
    const storageText = await initialStorageInput.getAttribute('value');
    expect(
      storageText,
      'The input text should be equal to `Test initial storage`',
    ).to.be.equal('Test initial storage');
  });

  it('should deploy the contract Storage', async () => {
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

  it('should open get_storage method', async () => {
    const methodBtn = await driver.findElement(
      By.xpath("//button[@data-testid='expand-method-btn-get_storage']"),
    );
    await methodBtn.click();

    const readBtn = await driver.findElement(
      By.xpath("//button[@data-testid='read-method-btn-get_storage']"),
    );

    await readBtn.click();

    const methodResponse = await driver.findElement(
      By.xpath("//*[@data-testid='method-response-get_storage']"),
    );

    expect(methodResponse, 'get_storage result should be visible').not.null;

    const methodResponseText = await driver
      .wait(
        until.elementTextContains(methodResponse, 'Test initial storage'),
        5000,
      )
      .getText();

    expect(
      methodResponseText.includes('Test initial storage'),
      'get_storage result should contain "Test initial storage"',
    ).to.be.true;
  });

  it('should call update_storage() method', async () => {
    const methodBtn = await driver.findElement(
      By.xpath("//button[@data-testid='expand-method-btn-update_storage']"),
    );
    await methodBtn.click();

    const newStorageInput = await driver.findElement(
      By.xpath("//input[@name='new_storage']"),
    );

    await newStorageInput.clear();
    await newStorageInput.sendKeys('Updated storage text');
    const newStorageText = await newStorageInput.getAttribute('value');
    expect(
      newStorageText,
      'The input text should be equal to `Updated storage text`',
    ).to.be.equal('Updated storage text');

    const writeBtn = await driver.findElement(
      By.xpath("//button[@data-testid='write-method-btn-update_storage']"),
    );

    await writeBtn.click();
  });
  after(() => driver.quit());
});
