import { By, until, WebDriver, type Locator } from 'selenium-webdriver';
import { E2E_CONFIG } from '../config';

export class BasePage {
  protected readonly driver: WebDriver;
  readonly path?: string;
  readonly visibleLocator?: Locator;

  public constructor(driver: WebDriver) {
    this.driver = driver;
  }

  get baseurl(): string | undefined {
    return this.path ? `${E2E_CONFIG.BASE_URL}${this.path}` : undefined;
  }

  async close() {
    await this.driver.quit();
  }

  async waitUntilVisible() {
    if (this.visibleLocator) {
      await this.driver.wait(until.elementLocated(this.visibleLocator), 2000);
    }
  }

  async skipTutorial() {
    const locator = By.xpath("//button[@data-testid='tutorial-skip-btn']");
    return this.driver.wait(until.elementLocated(locator), 5000).click();
  }

  async navigate() {
    if (this.baseurl) {
      await this.driver.navigate().to(this.baseurl);
    }
  }
}
