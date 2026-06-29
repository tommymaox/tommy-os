import { test, expect } from '@playwright/test';

test.describe('critical journeys', () => {
  test('theme switching persists', async ({ page }) => {
    await page.goto('/');
    await page.locator('#themeToggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', /light|dark/);
  });

  test('can create edit and delete an event', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: '+ Add' }).click();
    await page.locator('#fTitle').fill('Playwright Event');
    await page.locator('#fDate').fill(new Date().toISOString().slice(0, 10));
    await page.getByRole('button', { name: 'Save' }).click();
    await page.getByRole('button', { name: 'Events' }).click();
    await page.getByText('Playwright Event').click();
    await page.locator('#fTitle').fill('Playwright Event Updated');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Playwright Event Updated')).toBeVisible();
  });

  test('can add and edit a food entry', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Food' }).click();
    await page.getByRole('button', { name: '+ Add Food' }).click();
    await page.locator('#ftFoodName').fill('Playwright Chicken');
    await page.locator('#ftFoodServing').fill('100');
    await page.locator('#ftFoodKj').fill('500');
    await page.locator('#ftFoodProtein').fill('30');
    await page.getByRole('button', { name: 'Save' }).click();
    await page.getByRole('button', { name: 'Daily Log' }).click();
  });

  test('can edit training plans', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Training' }).click();
    await page.getByRole('button', { name: 'Gym' }).click();
    await expect(page.getByText('Push')).toBeVisible();
  });

  test('board is reachable and supports cards', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Board' }).click();
    await page.getByRole('button', { name: '+ New Card' }).click();
    await page.locator('#kbFTitle').fill('Playwright card');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Playwright card')).toBeVisible();
  });
});
