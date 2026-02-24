import { By, type Locator, until } from 'selenium-webdriver';
import { BasePage } from './BasePage';

export class ContractsPage extends BasePage {
  override path = '/contracts';
  override visibleLocator: Locator = By.xpath(
    "//*[@data-testid='contracts-page-title']",
  );

  async openContract(name: string) {
    const locator = By.xpath(
      `//*[@data-testid='contract-file' and contains(text(), '${name}')]`,
    );
    return this.driver.wait(until.elementLocated(locator), 2000).click();
  }
}
