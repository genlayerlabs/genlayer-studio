import { By, type Locator, WebElement, until } from 'selenium-webdriver';
import { Select } from 'selenium-webdriver/lib/select';
import { BasePage } from './BasePage';

export class SettingsPage extends BasePage {
  override path = '/settings';
  override visibleLocator: Locator = By.xpath(
    "//*[@data-testid='settings-page-title']",
  );

  async openNewProviderModal() {
    const locator = By.xpath(
      "//button[@data-testid='create-new-provider-btn']",
    );
    return this.driver.wait(until.elementLocated(locator), 5000).click();
  }

  async getProvidersElements(): Promise<WebElement[]> {
    return this.driver.findElements(
      By.xpath("//div[@data-testid='provider-item']"),
    );
  }

  async getFirstProviderWithModelText(
    text: string,
  ): Promise<WebElement | null> {
    const elements = await this.driver.findElements(
      By.xpath("//div[@data-testid='provider-item']"),
    );

    for (const element of elements) {
      const modelSpan = await element.findElement(
        By.xpath(".//span[@data-testid='provider-item-model']"),
      );
      const elementText = await modelSpan.getText();
      if (elementText.includes(text)) {
        return element;
      }
    }

    return null;
  }

  async createCustomProvider({
    provider,
    model,
    plugin,
  }: {
    provider: string;
    model: string;
    plugin: string;
  }) {
    await this.openNewProviderModal();

    const toggleCustomProviderBtn = await this.driver.findElement(
      By.xpath("//button[@data-testid='toggle-custom-provider']"),
    );

    await toggleCustomProviderBtn.click();
    await this.driver.sleep(100);

    const inputProviderElement = await this.driver.findElement(
      By.xpath("//input[@data-testid='input-provider']"),
    );
    const inputModelElement = await this.driver.findElement(
      By.xpath("//input[@data-testid='input-model']"),
    );
    const selectPluginElement = await this.driver.findElement(
      By.xpath("//select[@data-testid='input-plugin']"),
    );

    inputProviderElement.click();
    inputProviderElement.sendKeys(provider);

    inputModelElement.click();
    inputModelElement.sendKeys(model);

    const selectPlugin = new Select(selectPluginElement);
    await selectPlugin.selectByValue(plugin);

    const createProviderBtn = await this.driver.wait(
      until.elementLocated(
        By.xpath("//button[@data-testid='btn-create-provider']"),
      ),
    );

    await createProviderBtn.click();

    await this.driver.navigate().refresh();
  }

  async createProvider({
    provider,
    model,
  }: {
    provider: string;
    model: string;
  }) {
    await this.openNewProviderModal();
    await this.driver.sleep(1000);

    // provider select
    const selectProviderElement = await this.driver.wait(
      until.elementLocated(
        By.xpath("//select[contains(@data-testid, 'input-provider')]"),
      ),
    );
    await this.driver.sleep(1000);

    const selectProvider = new Select(selectProviderElement);
    await selectProvider.selectByValue(provider);
    await this.driver.sleep(1000);

    // model select
    const selectModelElement = await this.driver.wait(
      until.elementLocated(
        By.xpath("//select[contains(@data-testid, 'input-model')]"),
      ),
    );
    const selectModel = new Select(selectModelElement);
    await selectModel.selectByValue(model);
    await this.driver.sleep(1000);

    // More?
    const createValidatorBtn = await this.driver.wait(
      until.elementLocated(
        By.xpath("//button[@data-testid='btn-create-provider']"),
      ),
    );

    await createValidatorBtn.click();
  }
}
