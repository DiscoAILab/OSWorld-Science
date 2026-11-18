// ECON-046 reference and grader: Portfolio Performance 0.89.0's own model, investment-plan generator and performance engine.
//   build <fund_prices.csv> <out.xml>   construct the client with the savings plan, let PP generate the executions, save XML, print
//   calc  <file.xml>                    load a saved client (any PP file format), print measures and a summary of its contents
import java.io.*;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import name.abuchen.portfolio.model.*;
import name.abuchen.portfolio.money.*;
import name.abuchen.portfolio.snapshot.ClientSnapshot;
import name.abuchen.portfolio.snapshot.PerformanceIndex;
import name.abuchen.portfolio.util.Interval;

public class PPRef2 {
    static final LocalDate END = LocalDate.of(2025, 12, 31);

    static String q(String s) { return "\"" + (s == null ? "" : s.replace("\\", "/").replace("\"", "'")) + "\""; }

    public static void main(String[] a) throws Exception {
        Client c;
        if (a[0].equals("build")) {
            c = new Client(); c.setBaseCurrency("EUR");
            Security s = new Security("Westland Sustainable Equity Fund", "EUR"); s.setIsin("XS0543210982"); c.addSecurity(s);
            List<String> lines = Files.readAllLines(Paths.get(a[1]));
            for (String l : lines.subList(1, lines.size())) {
                String[] r = l.split(",");
                s.addPrice(new SecurityPrice(LocalDate.parse(r[0]), Values.Quote.factorize(Double.parseDouble(r[1]))));
            }
            Account acc = new Account("Cash account"); acc.setCurrencyCode("EUR"); c.addAccount(acc);
            Portfolio pf = new Portfolio("Securities account"); pf.setReferenceAccount(acc); c.addPortfolio(pf);
            InvestmentPlan p = new InvestmentPlan("Savings plan");
            p.setType(InvestmentPlan.Type.PURCHASE_OR_DELIVERY);
            p.setSecurity(s); p.setPortfolio(pf); p.setAccount(null);
            p.setStart(LocalDate.of(2023, 1, 15)); p.setInterval(1);
            p.setAmount(Values.Amount.factorize(300)); p.setFees(Values.Amount.factorize(1.5)); p.setAutoGenerate(true);
            c.addPlan(p);
            p.generateTransactions(new CurrencyConverterImpl(new ExchangeRateProviderFactory(c), "EUR"));
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
        o.append(",\"ttwror_pa_pct\":").append(100 * ix.getFinalAccumulatedAnnualizedPercentage());
        o.append(",\"irr_pct\":").append(100 * ix.getPerformanceIRR());
        o.append(",\"absolute_performance\":").append(delta[delta.length - 1] / Values.Amount.divider());
        o.append(",\"market_value_end\":").append(snap.getMonetaryAssets().getAmount() / Values.Amount.divider());
        o.append(",\"base_currency\":").append(q(c.getBaseCurrency()));
        o.append(",\"securities\":[");
        for (Security x : c.getSecurities()) o.append("{\"name\":").append(q(x.getName())).append(",\"isin\":").append(q(x.getIsin())).append(",\"currency\":").append(q(x.getCurrencyCode()))
            .append(",\"prices\":").append(x.getPrices().size())
            .append(",\"first\":\"").append(x.getPrices().isEmpty() ? "" : x.getPrices().get(0).getDate()).append("\",\"last\":\"").append(x.getPrices().isEmpty() ? "" : x.getPrices().get(x.getPrices().size() - 1).getDate())
            .append("\",\"close_2025_12_31\":").append(x.getSecurityPrice(END).getValue() / Values.Quote.divider()).append("},");
        if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
        o.append("],\"plans\":[");
        for (InvestmentPlan p : c.getPlans()) o.append("{\"name\":").append(q(p.getName())).append(",\"type\":").append(q(String.valueOf(p.getPlanType())))
            .append(",\"isin\":").append(q(p.getSecurity() == null ? "" : p.getSecurity().getIsin())).append(",\"portfolio\":").append(q(p.getPortfolio() == null ? "" : p.getPortfolio().getName()))
            .append(",\"account\":").append(q(p.getAccount() == null ? "" : p.getAccount().getName())).append(",\"start\":\"").append(p.getStart()).append("\",\"interval\":").append(p.getInterval())
            .append(",\"amount\":").append(p.getAmount() / Values.Amount.divider()).append(",\"fees\":").append(p.getFees() / Values.Amount.divider()).append(",\"taxes\":").append(p.getTaxes() / Values.Amount.divider())
            .append(",\"auto\":").append(p.isAutoGenerate()).append(",\"transactions\":").append(p.getTransactions().size()).append("},");
        if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
        o.append("],\"account_tx\":[");
        for (Account x : c.getAccounts()) for (AccountTransaction t : x.getTransactions())
            o.append("[\"").append(t.getDateTime().toLocalDate()).append("\",\"").append(t.getType()).append("\",").append(t.getAmount() / Values.Amount.divider()).append("],");
        if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
        o.append("],\"portfolio_tx\":[");
        for (Portfolio x : c.getPortfolios()) for (PortfolioTransaction t : x.getTransactions())
            o.append("[\"").append(t.getDateTime().toLocalDate()).append("\",\"").append(t.getType()).append("\",\"").append(t.getSecurity() == null ? "" : t.getSecurity().getIsin()).append("\",").append(t.getShares() / Values.Share.divider())
             .append(",").append(t.getAmount() / Values.Amount.divider()).append(",").append(t.getUnitSum(Transaction.Unit.Type.FEE).getAmount() / Values.Amount.divider()).append("],");
        if (o.charAt(o.length() - 1) == ',') o.setLength(o.length() - 1);
        o.append("],\"warnings\":").append(warn.size()).append("}");
        System.out.println("### PPREF " + o);
    }
}
