type PortfolioPosition = { notional_usd: number; open_pnl_usd?: number; quote_status?: string };
type PortfolioTrade = { opened_at: number; closed_at?: number; pnl_usd?: number };
type PortfolioBook = {
  name: string; starting_balance: number; balance: number; portfolio_group?: string;
  position: PortfolioPosition | null; positions?: PortfolioPosition[]; history: PortfolioTrade[];
};
type PortfolioStats = {
  equity: number; trades: number; wins: number; valuation_stale?: boolean;
};

export function portfolioBookPositions<Position extends PortfolioPosition>(book: {
  position: Position | null; positions?: Position[];
}): Position[] {
  return book.positions?.length ? book.positions : book.position ? [book.position] : [];
}

// Only this explicitly selected cohort belongs to the main PAPER account.
// TEST strategies and legacy exits keep their separate capital and histories.
export function promotedPaperPortfolio<Book extends PortfolioBook>(lab?: {
  books: Record<string, Book>; stats: Record<string, PortfolioStats>;
  portfolio_setup?: { status?: string; strategies?: string[] };
}) {
  const setup = lab?.portfolio_setup;
  if (!lab || setup?.status !== 'ACTIVE') return null;
  const ids = [...new Set(setup.strategies || [])];
  if (!ids.length || ids.some(id => !lab.books[id] || lab.books[id].portfolio_group !== 'PROMOTED_PAPER')) return null;
  const books = ids.map(id => ({ id, book: lab.books[id], stats: lab.stats[id] }));
  if (books.some(({ book, stats }) => !Number.isFinite(book.starting_balance) || book.starting_balance <= 0
    || !Number.isFinite(book.balance) || (stats && !Number.isFinite(stats.equity)))) return null;
  const startingBalance = books.reduce((sum, { book }) => sum + book.starting_balance, 0);
  const balance = books.reduce((sum, { book }) => sum + book.balance, 0);
  const equity = books.reduce((sum, { book, stats }) => sum + (stats?.equity ?? (book.balance + portfolioBookPositions(book).reduce((pnl, p) => pnl + (p.open_pnl_usd ?? 0), 0))), 0);
  const reserved = books.reduce((sum, { book }) => sum + portfolioBookPositions(book).reduce((value, p) => value + p.notional_usd, 0), 0);
  const trades = books.reduce((sum, { book, stats }) => sum + (stats?.trades ?? book.history.length), 0);
  const wins = books.reduce((sum, { book, stats }) => sum + (stats?.wins ?? book.history.filter(trade => (trade.pnl_usd ?? 0) > 0).length), 0);
  const history = books.flatMap(({ id, book }) => book.history.map(trade => ({
    ...trade as Book['history'][number], strategy_id: id, strategy_name: book.name,
  }))).sort((left, right) => (right.closed_at ?? right.opened_at) - (left.closed_at ?? left.opened_at));
  return {
    books, history, startingBalance, balance, equity, reserved,
    available: Math.max(0, balance - reserved),
    realizedPnl: balance - startingBalance,
    unrealizedPnl: equity - balance,
    returnPct: ((equity - startingBalance) / startingBalance) * 100,
    openPositions: books.reduce((count, {book}) => count + portfolioBookPositions(book).length, 0),
    trades, wins, winRate: trades ? (wins / trades) * 100 : 0,
    valuationStale: books.some(({ book, stats }) => Boolean(stats?.valuation_stale)
      || portfolioBookPositions(book).some(position => position.quote_status !== 'fresh')),
  };
}
