import { expect, test } from '@playwright/test'

/**
 * 归属受控爬虫批次数据集 E2E：
 * 通过页面顶部身份切换模拟两个团队的读写/只读账户，
 * 覆盖归属可见性、只读拦截、数据集幂等、事务回滚、交接冲突裁决与快照边界。
 */
test.describe('归属受控批次数据集', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/batchscope')
    await page.getByTestId('identity-bar').waitFor()
  })

  test('身份切换后只看到本团队归属任务与批次，无归属任务不可启动', async ({ page }) => {
    await page.getByTestId('tab-ownership').click()
    await expect(page.getByTestId('own-row-1')).toBeVisible()

    // 切到团队乙：任务 1 归属团队甲，不应出现在乙的归属清单。
    await page.getByTestId('team-select').selectOption('TEAM-B')
    await expect(page.getByTestId('own-row-1')).toHaveCount(0)
    await expect(page.getByTestId('own-row-2')).toBeVisible()

    await page.getByTestId('tab-runs').click()
    const runRows = page.locator('[data-testid^="run-row-"]')
    // 乙只看到自己归属的 RUN-0003，无归属 RUN-0006 与甲的批次都不出现。
    await expect(runRows).toHaveCount(1)
    await expect(page.getByTestId('run-row-3')).toContainText('RUN-0003')
  })

  test('只读账户的写操作被爬虫引擎拒绝并展示错误', async ({ page }) => {
    await page.getByTestId('role-select').selectOption('readonly')
    await page.getByTestId('tab-runs').click()
    await page.getByTestId('start-run').click()
    await expect(page.getByTestId('error-banner')).toContainText('只读账户不能改动')

    await page.getByTestId('tab-datasets').click()
    await page.getByTestId('idempotency-key').fill('E2E-RO')
    await page.getByTestId('create-dataset').click()
    await expect(page.getByTestId('error-banner')).toContainText('只读账户')
  })

  test('数据集生成、幂等只记一次、版本切换保持字段契约', async ({ page }) => {
    await page.getByTestId('tab-datasets').click()
    await page.getByTestId('dataset-task').selectOption('1')
    await page.getByTestId('idempotency-key').fill('E2E-IDEM')
    await page.getByTestId('create-dataset').click()

    const firstRow = page.locator('[data-testid^="ds-row-"]').first()
    await expect(firstRow).toContainText('BSET-')
    await expect(firstRow).toContainText('RUN-0001')
    await expect(firstRow).toContainText('生效中')

    // 第二次相同提交：只记一次，提示幂等回执。
    await page.getByTestId('create-dataset').click()
    await expect(page.getByTestId('notice-banner')).toContainText('幂等命中')
    await expect(page.locator('[data-testid^="ds-row-"]')).toHaveCount(1)

    // 再生成一个新版本：旧版本自动归档。
    await page.getByTestId('idempotency-key').fill('E2E-IDEM-2')
    await page.getByTestId('create-dataset').click()
    await expect(page.locator('[data-testid^="ds-row-"]')).toHaveCount(2)
    const archived = page.locator('[data-status="已归档"]')
    await expect(archived).toHaveCount(1)

    // 切回旧版本并核对输入输出字段契约未变。
    const oldRow = page.locator('[data-status="已归档"]').first()
    const oldId = (await oldRow.getAttribute('data-testid'))?.replace('ds-row-', '') ?? ''
    await page.getByTestId(`ds-activate-${oldId}`).click()
    await expect(page.getByTestId(`ds-status-${oldId}`)).toContainText('生效中')
    await page.getByTestId(`ds-detail-${oldId}`).click()
    await expect(page.getByTestId('ds-detail-box')).toContainText('团队编号')
    await expect(page.getByTestId('ds-detail-box')).toContainText('回执编号')
  })

  test('快照失败时数据集同事务回滚，不留半成品', async ({ page }) => {
    await page.getByTestId('tab-datasets').click()
    await page.getByTestId('dataset-task').selectOption('1')
    await page.getByTestId('idempotency-key').fill('E2E-ROLLBACK')
    const before = await page.locator('[data-testid^="ds-row-"]').count()
    await page.getByTestId('create-dataset-fail').click()
    await expect(page.getByTestId('error-banner')).toContainText('快照')
    await expect(page.locator('[data-testid^="ds-row-"]')).toHaveCount(before)
  })

  test('交接冻结写权限，完成后裁决冲突并只产出一份权威批次', async ({ page }) => {
    // 团队甲对任务 3 发起交接给团队乙。
    await page.getByTestId('tab-ownership').click()
    await page.getByTestId('handover-open-3').click()
    await expect(page.getByTestId('handover-modal')).toBeVisible()
    await page.getByTestId('handover-to-team').selectOption('TEAM-B')
    await page.getByTestId('submit-handover').click()
    await expect(page.getByTestId('handover-modal')).toHaveCount(0)

    // 交接期间启动新批次被引擎拒绝。
    await page.getByTestId('tab-runs').click()
    await page.getByTestId('new-run-task').selectOption('3')
    await page.getByTestId('start-run').click()
    await expect(page.getByTestId('error-banner')).toContainText('交接')

    // 回到交接页完成裁决。
    await page.getByTestId('tab-ownership').click()
    const pendingRow = page.locator('[data-testid^="handover-row-"]', { hasText: '待交接' }).first()
    const handoverId = (await pendingRow.getAttribute('data-testid'))?.replace('handover-row-', '') ?? ''
    await page.getByTestId(`handover-complete-${handoverId}`).click()
    await expect(page.getByTestId(`handover-status-${handoverId}`)).toContainText('已完成')

    // 切到团队乙：权威批次 RUN-0004-R1 归属乙，乙可以启动新批次。
    await page.getByTestId('team-select').selectOption('TEAM-B')
    await page.getByTestId('tab-runs').click()
    await expect(
      page.locator('[data-testid^="run-row-"]').filter({ hasText: 'RUN-0004-R1' }),
    ).toHaveCount(1)

    // 乙生成数据集，只含权威批次，快照站点限定在乙的授权站点。
    await page.getByTestId('tab-datasets').click()
    await page.getByTestId('dataset-task').selectOption('3')
    await page.getByTestId('idempotency-key').fill('E2E-ARBITER')
    await page.getByTestId('create-dataset').click()
    const dsRow = page.locator('[data-testid^="ds-row-"]').filter({ hasText: 'RUN-0004-R1' }).first()
    await expect(dsRow).toBeVisible()
  })

  test('站点执行快照只包含当前团队授权站点，并落到跨业务面清单', async ({ page }) => {
    await page.getByTestId('tab-datasets').click()
    await page.getByTestId('dataset-task').selectOption('1')
    await page.getByTestId('idempotency-key').fill('E2E-SNAP')
    await page.getByTestId('create-dataset').click()

    await page.getByTestId('tab-snapshots').click()
    const snapRow = page.locator('[data-testid^="snap-row-"]').first()
    await expect(snapRow).toContainText('TARG-0001')
    await expect(snapRow).toContainText('TARG-0002')
    await expect(snapRow).toContainText('跨业务面采集目标')

    await page.getByTestId('team-select').selectOption('TEAM-B')
    // 乙的快照只能出现乙的授权站点 TARG-0003，绝不能包含甲的站点。
    const bSnapRows = page.locator('[data-testid^="snap-row-"]')
    await expect(bSnapRows.filter({ hasText: 'TARG-0001' })).toHaveCount(0)
    await expect(bSnapRows.filter({ hasText: 'TARG-0002' })).toHaveCount(0)
  })

  test('审计页按团队核对归属、批次、交接与启动权限', async ({ page }) => {
    await page.getByTestId('tab-audit').click()
    await expect(page.locator('[data-testid^="audit-row-"]').first()).toBeVisible()
    await expect(page.getByTestId('audit-launch-1')).toContainText('允许启动')
    await page.getByTestId('role-select').selectOption('readonly')
    await expect(page.getByTestId('audit-launch-1')).toContainText('禁止启动')
  })
})
