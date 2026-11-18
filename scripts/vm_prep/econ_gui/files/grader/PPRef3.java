// ECON-049 reference and grader: Portfolio Performance 0.89.0's own model and performance engine.
//   build <inputs dir> <out.xml>   book the broker exports as the CSV import wizard does, record the 4-for-1 split of
//                                  2024-06-10 as the stock split wizard does with "update quotes" OFF (the vendor's
//                                  prices are already split-adjusted), save XML, print measures
//   calc  <file.xml>               load a saved client, print measures and a summary of its contents
import java.io.*;
import java.math.BigDecimal;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import name.abuchen.portfolio.model.*;
import name.abuchen.portfolio.money.*;
import name.abuchen.portfolio.snapshot.ClientSnapshot;
import name.abuchen.portfolio.snapshot.PerformanceIndex;
import name.abuchen.portfolio.snapshot.SecurityPosition;
import name.abuchen.portfolio.util.Interval;

public class PPRef3 {
    static final LocalDate END = LocalDate.of(2025, 12, 31), EX = LocalDate.of(2024, 6, 10);

    static long amt(String s) { return Values.Amount.factorize(Double.parseDouble(s)); }

    static String q(String s) { return "\"" + (s == null ? "" : s.replace("\\", "/").replace("\"", "'")) + "\""; }

    static List<String[]> csv(Path p) throws IOException {
        List<String[]> out = new ArrayList<>();
        for (String l : Files.readAllLines(p)) out.add(l.split(",", -1));
        return out.subList(1, out.size());
    }

    public static void main(String[] a) throws Exception {
        Client c;
        if (a[0].equals("build")) {
            Path in = Paths.get(a[1]);
            c = new Client(); c.setBaseCurrency("EUR");
            Account acc = new Account("Cash account"); acc.setCurrencyCode("EUR"); c.addAccount(acc);
            Portfolio pf = new Portfolio("Securities account"); pf.setReferenceAccount(acc); c.addPortfolio(pf);
            Map<String, Security> sec = new LinkedHashMap<>();
            for (String[] r : csv(in.resolve("portfolio_transactions.csv")))
                sec.computeIfAbsent(r[3], k -> { Security s = new Security(r[2], "EUR"); s.setIsin(k); c.addSecurity(s); return s; });
            Security s = sec.values().iterator().next();
            for (String[] r : csv(in.resolve("prices_vendor.csv")))
                s.addPrice(new SecurityPrice(LocalDate.parse(r[0]), Values.Quote.factorize(Double.parseDouble(r[1]))));
            for (String[] r : csv(in.resolve("portfolio_transactions.csv"))) {
                BuySellEntry b = new BuySellEntry(pf, acc);
                b.setType(r[1].equals("Buy") ? PortfolioTransaction.Type.BUY : PortfolioTransaction.Type.SELL);
                b.setDate(LocalDate.parse(r[0]).atStartOfDay());
                b.setSecurity(sec.get(r[3]));
                b.setShares(Values.Share.factorize(Double.parseDouble(r[4])));
                b.setAmount(amt(r[5]));
                b.setCurrencyCode("EUR");
                b.getPortfolioTransaction().addUnit(new Transaction.Unit(Transaction.Unit.Type.FEE, Money.of("EUR", amt(r[6]))));
                b.insert();
            }
            for (String[] r : csv(in.resolve("account_transactions.csv"))) {
                AccountTransaction t = new AccountTransaction();
                t.setType(switch (r[1]) { case "Deposit" -> AccountTransaction.Type.DEPOSIT; case "Withdrawal" -> AccountTransaction.Type.REMOVAL;
                    case "Dividend" -> AccountTransaction.Type.DIVIDENDS; case "Fees" -> AccountTransaction.Type.FEES; default -> throw new IllegalArgumentException(r[1]); });
                t.setDateTime(LocalDate.parse(r[0]).atStartOfDay());
                t.setAmount(amt(r[2])); t.setCurrencyCode("EUR");
                if (!r[8].isEmpty()) t.setNote(r[8]);
                if (r[1].equals("Dividend")) {
                    t.setSecurity(sec.get(r[5]));
                    t.setShares(Values.Share.factorize(Double.parseDouble(r[6])));
                    t.addUnit(new Transaction.Unit(Transaction.Unit.Type.TAX, Money.of("EUR", amt(r[7]))));
                }
                acc.addTransaction(t);
            }
            // the stock split wizard (StockSplitModel.applyChanges) with changeTransactions on, changeHistoricalQuotes off
            s.addEvent(new SecurityEvent(EX, SecurityEvent.Type.STOCK_SPLIT, "4:1"));
            for (TransactionPair<?> pair : s.getTransactions(c)) {
                Transaction t = pair.getTransaction();
                if (t.getDateTime().toLocalDate().isBefore(EX))
                    t.setShares(BigDecimal.valueOf(t.getShares()).multiply(BigDecimal.valueOf(4)).longValue());
            }
            ClientFactory.save(c, new File(a[2]));
        } else {
            c = ClientFactory.load(new File(a[1]), null, new org.eclipse.core.runtime.NullProgressMonitor());
        }
        CurrencyConverter conv = new CurrencyConverterImpl(new ExchangeRateProviderFactory(c), c.getBaseCurrency());
        List<Exception> warn = new ArrayList<>();
        PerformanceIndex ix = PerformanceIndex.forClient(c, conv, Interval.of(LocalDate.of(2022, 12, 31), END), warn);
        long[] delta = ix.calculateAbsoluteDelta();
        ClientSnapshot snap = ClientSnapshot.create(c, conv, END);
        StringBuilder o = new StringBuilder("{");
        o.append("\"ttwror_pct\":").append(100 * ix.getFinalAccumulatedPercentage());
        o.append(",\"irr_pct\":").append(100 * ix.getPerformanceIRR());
        o.append(",\"absolute_performance\":").append(delta[delta.length - 1] / Values.Amount.divider());
        o.append(",\"market_value_end\":").append(snap.getMonetaryAssets().getAmount() / Values.Amount.divider());
        o.append(",\"base_currency\":").append(q(c.getBaseCurrency()));
        o.append(",\"securities\":[");
        for (Security x : c.getSecurities()) {
            double pre = 0, post = 0;
            for (SecurityPrice p : x.getPrices()) { if (p.getDate().isBefore(EX)) pre += p.getValue() / Values.Quote.divider(); else post += p.getValue() / Values.Quote.divider(); }
            SecurityPosition pos = snap.getJointPortfolio().getPositionsBySecurity().get(x);
            o.append("{\"name\":").append(q(x.getName())).append(",\"isin\":").append(q(x.getIsin())).append(",\"currency\":").append(q(x.getCurrencyCode()))
             .append(",\"prices\":").append(x.getPrices().size())
             .append(",\"first\":\"").append(x.getPrices().isEmpty() ? "" : x.getPrices().get(0).getDate()).append("\",\"last\":\"").append(x.getPrices().isEmpty() ? "" : x.getPrices().get(x.getPrices().size() - 1).getDate())
             .append("\",\"price_sum_pre_split\":").append(Math.round(pre * 100) / 100.0).append(",\"price_sum_post_split\":").append(Math.round(post * 100) / 100.0)
             .append(",\"shares_end\":").append(pos == null ? 0 : pos.getShares() / Values.Share.divider())
             .append(",\"events\":[");
            for (SecurityEvent e : x.getEvents()) o.append("[\"").append(e.getDate()).append("\",\"").append(e.getType()).append("\",").append(q(e.getDetails())).append("],");
            if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
            o.append("]},");
        }
        if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
        o.append("],\"account_tx\":[");
        for (Account x : c.getAccounts()) for (AccountTransaction t : x.getTransactions())
            o.append("[\"").append(t.getDateTime().toLocalDate()).append("\",\"").append(t.getType()).append("\",").append(t.getAmount() / Values.Amount.divider())
             .append(",").append(t.getUnitSum(Transaction.Unit.Type.TAX).getAmount() / Values.Amount.divider()).append(",").append(t.getShares() / Values.Share.divider()).append("],");
        if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
        o.append("],\"portfolio_tx\":[");
        for (Portfolio x : c.getPortfolios()) for (PortfolioTransaction t : x.getTransactions())
            o.append("[\"").append(t.getDateTime().toLocalDate()).append("\",\"").append(t.getType()).append("\",\"").append(t.getSecurity().getIsin()).append("\",").append(t.getShares() / Values.Share.divider())
             .append(",").append(t.getAmount() / Values.Amount.divider()).append(",").append(t.getUnitSum(Transaction.Unit.Type.FEE).getAmount() / Values.Amount.divider()).append("],");
        if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
        o.append("],\"warnings\":").append(warn.size()).append("}");
        System.out.println("### PPREF " + o);
    }
}
