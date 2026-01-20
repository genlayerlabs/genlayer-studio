import { By, type Locator, WebElement } from 'selenium-webdriver';
import { BasePage } from './BasePage';

export class TutorialPage extends BasePage {
  override path = '/contracts';
  override visibleLocator: Locator = By.xpath(
    "//*[@data-testid='contracts-page-title']",
  );

  async getStepElement(target: string): Promise<WebElement> {
    const stepElement = await this.driver.findElement(
      By.xpath(`//div[@data-testid='tutorial-step-${target}']`),
    );

    return stepElement;
  }

  async getStepItems(
    stepElement: WebElement,
  ): Promise<{ header: WebElement; content: WebElement }> {
    const headerElement = await stepElement.findElement(
      By.xpath("//div[contains(@class, 'v-step__header')]/div"),
    );

    const contentElement = await stepElement.findElement(
      By.xpath("//div[contains(@class, 'v-step__content')]/div"),
    );

    return {
      header: headerElement,
      content: contentElement,
    };
  }

  async getNextButton(stepElement: WebElement): Promise<WebElement> {
    const nextButton = await stepElement.findElement(
      By.xpath(
        "//div[contains(@class, 'v-step__buttons')]/button[contains(@class, 'v-step__button-next')]",
      ),
    );

    return nextButton;
  }

  async getFinishButton(stepElement: WebElement): Promise<WebElement> {
    const finishButton = await stepElement.findElement(
      By.xpath(
        "//div[contains(@class, 'v-step__buttons')]/button[contains(@class, 'v-step__button-stop')]",
      ),
    );

    return finishButton;
  }
}
