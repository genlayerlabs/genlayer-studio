import { By, type Locator, WebElement, until } from 'selenium-webdriver';
import { Select } from 'selenium-webdriver/lib/select';
import { BasePage } from './BasePage';
import { expect } from 'chai';

export class ValidatorsPage extends BasePage {
  override path = '/validators';
  override visibleLocator: Locator = By.xpath(
    "//*[@data-testid='validators-page-title']",
  );

  async openNewValidatorModal() {
    const locator = By.xpath(
      "//button[@data-testid='create-new-validator-btn']",
    );
    return this.driver.wait(until.elementLocated(locator), 5000).click();
  }
  async getValidatorsElements(): Promise<WebElement[]> {
    return this.driver.findElements(
      By.xpath("//div[@data-testid='validator-item']"),
    );
  }

  async createValidator({
    provider,
    model,
    stake,
  }: {
    provider: string;
    model: string;
    stake: number;
  }) {
    // get the list of validators

    await this.openNewValidatorModal();
    // provider select
    const selectProviderElement = await this.driver.wait(
      until.elementLocated(
        By.xpath("//select[contains(@data-testid, 'dropdown-provider')]"),
      ),
    );
    const selectProvider = new Select(selectProviderElement);
    await selectProvider.selectByValue(provider);

    // model select
    const selectModelElement = await this.driver.wait(
      until.elementLocated(
        By.xpath("//select[contains(@data-testid, 'dropdown-model')]"),
      ),
    );
    const selectModel = new Select(selectModelElement);
    await selectModel.selectByValue(model);

    const stakeInput = await this.driver.wait(
      until.elementLocated(By.xpath("//input[@data-testid='input-stake']")),
    );
    await stakeInput.clear();
    await stakeInput.sendKeys(stake);

    const createValidatorBtn = await this.driver.wait(
      until.elementLocated(
        By.xpath("//button[@data-testid='btn-create-validator']"),
      ),
    );
    // call create validator button
    await createValidatorBtn.click();
    await this.driver.navigate().refresh();
  }

  async createValidatorIfRequired() {
    const initialValidators = await this.getValidatorsElements();
    if (initialValidators.length < 1) {
      await this.createValidator({
        provider: 'heuristai',
        model: 'mistralai/mixtral-8x7b-instruct',
        stake: 7,
      });
      const existingValidators = await this.getValidatorsElements();
      expect(
        existingValidators.length,
        'number of validators should be greather than old validators list',
      ).be.greaterThan(initialValidators.length);
    }
  }
}
