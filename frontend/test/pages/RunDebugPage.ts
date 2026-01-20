import { By, type Locator } from 'selenium-webdriver';
import { BasePage } from './BasePage';

export class RunDebugPage extends BasePage {
  override path = '/run-debug';
  override visibleLocator: Locator = By.xpath(
    "//*[@data-testid='run-debug-page-title']",
  );
}
