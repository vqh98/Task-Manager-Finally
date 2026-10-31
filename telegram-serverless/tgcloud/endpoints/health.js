// Safe deployment probe. Endpoints do not receive bot updates and therefore do
// not require switching the production webhook.
export default async function health(_input, ctx) {
  return {
    ok: true,
    service: 'taskadviser',
    runtime: 'telegram-serverless',
    authenticated: Boolean(ctx?.initData?.user),
  };
}
