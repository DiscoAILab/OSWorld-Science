// ECON-042 reference and grader: Portfolio Performance 0.89.0's own model and performance engine.
//   build <inputs dir> <out.xml>   construct the client exactly as the CSV import wizard books the rows, save XML, print measures
//   calc  <file.xml>               load a saved client (any PP file format), print measures and a summary of its contents
import java.io.*;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import name.abuchen.portfolio.model.*;
import name.abuchen.portfolio.money.*;
import name.abuchen.portfolio.snapshot.PerformanceIndex;
import name.abuchen.portfolio.util.Interval;

public class PPRef {
    static long amt(String s) { return Values.Amount.factorize(Double.parseDouble(s)); }

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
            for (Map.Entry<String, Security> e : sec.entrySet())
                for (String[] r : csv(in.resolve("prices_" + e.getKey() + ".csv")))
                    e.getValue().addPrice(new SecurityPrice(LocalDate.parse(r[0]), Values.Quote.factorize(Double.parseDouble(r[1]))));
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
            ClientFactory.save(c, new File(a[2]));
        } else {
            c = ClientFactory.load(new File(a[1]), null, new org.eclipse.core.runtime.NullProgressMonitor());
        }
        CurrencyConverter conv = new CurrencyConverterImpl(new ExchangeRateProviderFactory(c), c.getBaseCurrency());
        List<Exception> warn = new ArrayList<>();
        PerformanceIndex ix = PerformanceIndex.forClient(c, conv, Interval.of(LocalDate.of(2022, 12, 31), LocalDate.of(2025, 12, 31)), warn);
        long[] delta = ix.calculateAbsoluteDelta();
        StringBuilder s = new StringBuilder("{");
        s.append("\"ttwror_pct\":").append(100 * ix.getFinalAccumulatedPercentage());
        s.append(",\"irr_pct\":").append(100 * ix.getPerformanceIRR());
        s.append(",\"absolute_performance\":").append(delta[delta.length - 1] / Values.Amount.divider());
        s.append(",\"base_currency\":\"").append(c.getBaseCurrency()).append("\"");
        s.append(",\"securities\":[");
        for (Security x : c.getSecurities()) s.append("{\"name\":\"").append(x.getName().replace("\"", "'")).append("\",\"isin\":\"").append(x.getIsin()).append("\",\"prices\":").append(x.getPrices().size())
            .append(",\"first\":\"").append(x.getPrices().isEmpty() ? "" : x.getPrices().get(0).getDate()).append("\",\"last\":\"").append(x.getPrices().isEmpty() ? "" : x.getPrices().get(x.getPrices().size() - 1).getDate()).append("\"},");
        if (s.charAt(s.length() - 1) == ',') s.setLength(s.length() - 1);
        s.append("],\"account_tx\":[");
        for (Account x : c.getAccounts()) for (AccountTransaction t : x.getTransactions())
            s.append("[\"").append(t.getDateTime().toLocalDate()).append("\",\"").append(t.getType()).append("\",").append(t.getAmount() / Values.Amount.divider()).append(",").append(t.getUnitSum(Transaction.Unit.Type.TAX).getAmount() / Values.Amount.divider()).append("],");
        if (s.charAt(s.length() - 1) == ',') s.setLength(s.length() - 1);
        s.append("],\"portfolio_tx\":[");
        for (Portfolio x : c.getPortfolios()) for (PortfolioTransaction t : x.getTransactions())
            s.append("[\"").append(t.getDateTime().toLocalDate()).append("\",\"").append(t.getType()).append("\",\"").append(t.getSecurity().getIsin()).append("\",").append(t.getShares() / Values.Share.divider())
             .append(",").append(t.getAmount() / Values.Amount.divider()).append(",").append(t.getUnitSum(Transaction.Unit.Type.FEE).getAmount() / Values.Amount.divider()).append("],");
        if (s.charAt(s.length() - 1) == ',') s.setLength(s.length() - 1);
        s.append("],\"warnings\":").append(warn.size()).append("}");
        System.out.println("### PPREF " + s);
    }
}
