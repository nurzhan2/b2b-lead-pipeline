package kz.qorsa.leadgen.export;

import com.google.api.client.googleapis.javanet.GoogleNetHttpTransport;
import com.google.api.client.http.HttpRequestInitializer;
import com.google.api.client.json.gson.GsonFactory;
import com.google.api.services.sheets.v4.Sheets;
import com.google.api.services.sheets.v4.SheetsScopes;
import com.google.api.services.sheets.v4.model.AddBandingRequest;
import com.google.api.services.sheets.v4.model.AddConditionalFormatRuleRequest;
import com.google.api.services.sheets.v4.model.AddSheetRequest;
import com.google.api.services.sheets.v4.model.AutoResizeDimensionsRequest;
import com.google.api.services.sheets.v4.model.BandedRange;
import com.google.api.services.sheets.v4.model.BandingProperties;
import com.google.api.services.sheets.v4.model.BasicFilter;
import com.google.api.services.sheets.v4.model.BatchUpdateSpreadsheetRequest;
import com.google.api.services.sheets.v4.model.BatchUpdateSpreadsheetResponse;
import com.google.api.services.sheets.v4.model.BooleanCondition;
import com.google.api.services.sheets.v4.model.BooleanRule;
import com.google.api.services.sheets.v4.model.CellData;
import com.google.api.services.sheets.v4.model.CellFormat;
import com.google.api.services.sheets.v4.model.ClearValuesRequest;
import com.google.api.services.sheets.v4.model.Color;
import com.google.api.services.sheets.v4.model.ConditionValue;
import com.google.api.services.sheets.v4.model.ConditionalFormatRule;
import com.google.api.services.sheets.v4.model.DimensionRange;
import com.google.api.services.sheets.v4.model.GridProperties;
import com.google.api.services.sheets.v4.model.GridRange;
import com.google.api.services.sheets.v4.model.Request;
import com.google.api.services.sheets.v4.model.RepeatCellRequest;
import com.google.api.services.sheets.v4.model.SetBasicFilterRequest;
import com.google.api.services.sheets.v4.model.Sheet;
import com.google.api.services.sheets.v4.model.SheetProperties;
import com.google.api.services.sheets.v4.model.Spreadsheet;
import com.google.api.services.sheets.v4.model.TextFormat;
import com.google.api.services.sheets.v4.model.UpdateSheetPropertiesRequest;
import com.google.api.services.sheets.v4.model.ValueRange;
import com.google.auth.http.HttpCredentialsAdapter;
import com.google.auth.oauth2.GoogleCredentials;
import jakarta.annotation.PostConstruct;
import java.io.FileInputStream;
import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.LeadSource;
import kz.qorsa.leadgen.domain.LeadStatus;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.service.ContactEnrichmentService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

/**
 * Pushes the current lead list into a Google Sheet as a set of tabs, using a
 * service account (no interactive OAuth, no MCP - a plain scheduled/REST
 * server-to-server sync). Disables itself cleanly when
 * {@link SheetsExportProperties#isConfigured()} is false: the core keeps
 * working, this just becomes a no-op with one WARN at startup.
 *
 * <p>Each sync fully rewrites every tab's data (clear + write) rather than
 * appending, which is what keeps re-running this every few minutes
 * idempotent without ever producing duplicate rows. Structural formatting
 * (banding, conditional format rules) is only ever added once - the first
 * time a tab is created - using a generous fixed row range, so Google
 * Sheets keeps applying it to whatever data occupies those rows on every
 * later sync without needing to be re-added (re-adding banding/conditional
 * rules on an existing range throws, so doing that every 5 minutes would
 * break after the first run).
 */
@Service
@Slf4j
public class SheetsExporter {

    private static final String APPLICATION_NAME = "qorsa-leadgen";
    private static final int COLUMN_COUNT = 14;
    // Fixed upper bound for banding/conditional-format ranges so they never
    // need to be recreated as the data grows or shrinks between syncs.
    private static final int MAX_FORMAT_ROWS = 10_000;

    // "Контакт" sits right after the phone columns on purpose: it is read
    // together with them. It holds a named human - the responsible official
    // from a госзакупка notice, or the CEO from ЕГРЮЛ - which is what turns
    // a 495 switchboard number into a call you can actually get through.
    private static final List<Object> HEADER = List.of(
            "Дата", "Компания", "Ниша", "Телефон", "Тип телефона", "Контакт", "Email", "Мессенджер",
            "Город", "Источник", "Score", "Статус", "Причина", "Ссылка");

    private static final DateTimeFormatter DATE_FORMAT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(ZoneOffset.UTC);

    // --- Site-audit tab -----------------------------------------------
    // A separate tab rather than extra columns on the lead tabs, because it
    // answers a different question. The lead tabs say "who to call"; this one
    // says "what is wrong with their site", which is what the call actually
    // opens with. It also lists companies whose site was measured and found
    // fine - they score nothing and would be invisible everywhere else, but
    // "checked, site is OK" and "never checked" are different facts and the
    // difference matters when you're deciding whether to phone someone.
    private static final String AUDIT_TAB = "🔍 Аудит сайтов";
    private static final int AUDIT_COLUMN_COUNT = 12;
    private static final List<Object> AUDIT_HEADER = List.of(
            "Компания", "Сайт", "PageSpeed", "Провалов", "Что не так с сайтом",
            "Телефон", "Email", "Город", "Источник", "Score", "Статус", "Проверен");

    private static final Color HEADER_BG = rgb(0.102, 0.102, 0.180);
    private static final Color WHITE = rgb(1, 1, 1);
    private static final Color BAND_GRAY = rgb(0.945, 0.945, 0.945);
    private static final Color SCORE_GREEN = rgb(0.714, 0.882, 0.722);
    private static final Color SCORE_YELLOW = rgb(1.0, 0.949, 0.6);
    private static final Color SCORE_GRAY = rgb(0.90, 0.90, 0.90);
    private static final Color STATUS_RED = rgb(0.859, 0.204, 0.204);
    private static final Color STATUS_ORANGE = rgb(0.902, 0.494, 0.133);
    private static final Color STATUS_GRAY = rgb(0.45, 0.45, 0.45);

    private final SheetsExportProperties properties;
    private final LeadRepository leadRepository;
    private Sheets sheetsService;

    public SheetsExporter(SheetsExportProperties properties, LeadRepository leadRepository) {
        this.properties = properties;
        this.leadRepository = leadRepository;
    }

    @PostConstruct
    void init() {
        if (!properties.isConfigured()) {
            log.warn("Sheets export disabled: set GOOGLE_CREDENTIALS_PATH and SHEETS_SPREADSHEET_ID to enable it");
            return;
        }
        try {
            this.sheetsService = buildSheetsService(properties.getCredentialsPath());
            log.info("Sheets export enabled, target spreadsheet {}", properties.getSpreadsheetId());
        } catch (IOException | GeneralSecurityException e) {
            log.error("Failed to initialize Google Sheets client - export disabled", e);
            this.sheetsService = null;
        }
    }

    @Scheduled(fixedDelayString = "${leadgen.sheets.fixed-delay-ms:300000}")
    public void syncScheduled() {
        if (sheetsService == null) {
            return; // disabled - the reason was already logged once at startup
        }
        SyncResult result = syncLeads();
        log.info("Scheduled Sheets sync: tabs={}, leads={}", result.tabsUpdated(), result.leadsExported());
    }

    /** Entry point for both the scheduled job and the manual REST trigger. */
    public SyncResult syncLeads() {
        if (sheetsService == null) {
            return new SyncResult(false, 0, 0,
                    "Sheets export disabled: GOOGLE_CREDENTIALS_PATH/SHEETS_SPREADSHEET_ID not set");
        }
        try {
            return doSync();
        } catch (IOException e) {
            log.error("Sheets sync failed", e);
            return new SyncResult(true, 0, 0, "Sheets sync failed: " + e.getMessage());
        }
    }

    private SyncResult doSync() throws IOException {
        List<Lead> leads = leadRepository.findAllWithCompany();

        Map<String, List<Lead>> tabs = new LinkedHashMap<>();
        tabs.put("🔥 Тёплые", leads.stream().filter(l -> isWarm(l.getStatus())).toList());
        tabs.put("❄️ Холодные", leads.stream().filter(l -> !isWarm(l.getStatus())).toList());
        tabs.put("📊 Все", leads);

        // --- Call lists ---------------------------------------------------
        // Three questions a caller actually asks, as three tabs. They overlap
        // on purpose: a company with no site, a known director and a mobile
        // number belongs on all of them, and that is the best lead this
        // system produces.
        tabs.put("🚫 Без сайта",
                leads.stream().filter(l -> !l.getCompany().isHasSite()).toList());
        // "Measured and bad", never "unmeasured" - a site nobody checked is
        // not a site that needs work, and listing it would waste a call.
        tabs.put("🔧 Сайт на доработку",
                leads.stream().filter(SheetsExporter::siteNeedsWork).toList());
        tabs.put("👤 Есть руководитель",
                leads.stream().filter(l -> !contactPerson(l.getCompany()).isEmpty()).toList());

        for (Lead lead : leads) {
            tabs.computeIfAbsent(sourceLabel(lead.getCompany().getSource()), k -> new ArrayList<>()).add(lead);
        }

        Spreadsheet spreadsheet = sheetsService.spreadsheets().get(properties.getSpreadsheetId()).execute();
        Map<String, Integer> titleToSheetId = new LinkedHashMap<>();
        for (Sheet sheet : spreadsheet.getSheets()) {
            titleToSheetId.put(sheet.getProperties().getTitle(), sheet.getProperties().getSheetId());
        }

        List<String> missingTitles = tabs.keySet().stream().filter(t -> !titleToSheetId.containsKey(t)).toList();
        if (!missingTitles.isEmpty()) {
            titleToSheetId.putAll(createSheets(missingTitles));
        }

        int leadsExported = 0;
        for (Map.Entry<String, List<Lead>> entry : tabs.entrySet()) {
            String title = entry.getKey();
            List<Lead> tabLeads = entry.getValue();
            int sheetId = titleToSheetId.get(title);
            boolean isNewSheet = missingTitles.contains(title);
            writeSheet(title, sheetId, tabLeads, isNewSheet);
            leadsExported += tabLeads.size();
        }

        int tabCount = tabs.size() + writeAuditTab(leads, titleToSheetId);
        return new SyncResult(true, tabCount, leadsExported, "ok");
    }

    /**
     * Writes the site-audit tab, creating it on first sync. Returns 1 if it
     * was written, 0 if nothing has been audited yet - an empty tab would be
     * more confusing than no tab.
     *
     * <p>Its rows aren't counted in leadsExported: the same leads already
     * appear on the lead tabs, and double-counting them would make the sync
     * log read as if the export had grown.
     */
    private int writeAuditTab(List<Lead> leads, Map<String, Integer> titleToSheetId) throws IOException {
        List<List<Object>> rows = buildAuditRows(leads);
        if (rows.size() <= 1) {
            return 0;
        }

        boolean isNew = !titleToSheetId.containsKey(AUDIT_TAB);
        int sheetId = isNew
                ? createSheets(List.of(AUDIT_TAB)).get(AUDIT_TAB)
                : titleToSheetId.get(AUDIT_TAB);

        writeRows(AUDIT_TAB, sheetId, rows, AUDIT_COLUMN_COUNT, isNew, false);
        return 1;
    }

    private Map<String, Integer> createSheets(List<String> titles) throws IOException {
        List<Request> requests = titles.stream()
                .map(title -> new Request().setAddSheet(new AddSheetRequest()
                        .setProperties(new SheetProperties().setTitle(title))))
                .toList();

        BatchUpdateSpreadsheetResponse response = sheetsService.spreadsheets()
                .batchUpdate(properties.getSpreadsheetId(), new BatchUpdateSpreadsheetRequest().setRequests(requests))
                .execute();

        Map<String, Integer> created = new LinkedHashMap<>();
        for (var reply : response.getReplies()) {
            if (reply.getAddSheet() != null) {
                SheetProperties props = reply.getAddSheet().getProperties();
                created.put(props.getTitle(), props.getSheetId());
            }
        }
        return created;
    }

    private void writeSheet(String title, int sheetId, List<Lead> leads, boolean isNewSheet) throws IOException {
        writeRows(title, sheetId, buildRows(leads), COLUMN_COUNT, isNewSheet, true);
    }

    /**
     * Clears a tab and rewrites it. Column count is a parameter rather than a
     * constant because the audit tab has its own shape; {@code leadFormatting}
     * switches on the Score/Status conditional-format rules, which only make
     * sense where those columns sit at their lead-tab indices.
     */
    private void writeRows(String title, int sheetId, List<List<Object>> rows, int columnCount,
                           boolean isNewSheet, boolean leadFormatting) throws IOException {
        String spreadsheetId = properties.getSpreadsheetId();
        String lastColumn = columnLetter(columnCount);

        sheetsService.spreadsheets().values()
                .clear(spreadsheetId, quoted(title) + "!A1:" + lastColumn + (MAX_FORMAT_ROWS + 1),
                        new ClearValuesRequest())
                .execute();

        sheetsService.spreadsheets().values()
                .update(spreadsheetId, quoted(title) + "!A1", new ValueRange().setValues(rows))
                .setValueInputOption("RAW")
                .execute();

        List<Request> formatting = buildFormattingRequests(sheetId, isNewSheet, columnCount, leadFormatting);
        sheetsService.spreadsheets()
                .batchUpdate(spreadsheetId, new BatchUpdateSpreadsheetRequest().setRequests(formatting))
                .execute();
    }

    /** 1 -> "A", 12 -> "L", 13 -> "M". Only ever called with small counts. */
    private static String columnLetter(int columnCount) {
        return String.valueOf((char) ('A' + columnCount - 1));
    }

    private List<Request> buildFormattingRequests(int sheetId, boolean isNewSheet, int columnCount,
                                                  boolean leadFormatting) {
        List<Request> requests = new ArrayList<>();

        requests.add(new Request().setRepeatCell(new RepeatCellRequest()
                .setRange(columnRange(sheetId, 0, columnCount, 0, 1))
                .setCell(new CellData().setUserEnteredFormat(new CellFormat()
                        .setBackgroundColor(HEADER_BG)
                        .setTextFormat(new TextFormat().setBold(true).setForegroundColor(WHITE))))
                .setFields("userEnteredFormat(backgroundColor,textFormat)")));

        requests.add(new Request().setUpdateSheetProperties(new UpdateSheetPropertiesRequest()
                .setProperties(new SheetProperties().setSheetId(sheetId)
                        .setGridProperties(new GridProperties().setFrozenRowCount(1)))
                .setFields("gridProperties.frozenRowCount")));

        requests.add(new Request().setSetBasicFilter(new SetBasicFilterRequest()
                .setFilter(new BasicFilter().setRange(
                        columnRange(sheetId, 0, columnCount, 0, MAX_FORMAT_ROWS)))));

        requests.add(new Request().setAutoResizeDimensions(new AutoResizeDimensionsRequest()
                .setDimensions(new DimensionRange().setSheetId(sheetId).setDimension("COLUMNS")
                        .setStartIndex(0).setEndIndex(columnCount))));

        if (isNewSheet) {
            requests.add(new Request().setAddBanding(new AddBandingRequest()
                    .setBandedRange(new BandedRange()
                            .setRange(columnRange(sheetId, 0, columnCount, 0, MAX_FORMAT_ROWS))
                            .setRowProperties(new BandingProperties()
                                    .setHeaderColor(HEADER_BG)
                                    .setFirstBandColor(WHITE)
                                    .setSecondBandColor(BAND_GRAY)))));

            if (leadFormatting) {
                requests.addAll(scoreConditionalFormatRules(sheetId));
                requests.addAll(statusConditionalFormatRules(sheetId));
            } else {
                requests.addAll(auditConditionalFormatRules(sheetId));
            }
        }

        return requests;
    }

    /**
     * Audit tab colouring, on the two measured columns.
     *
     * <p>PageSpeed (index 2) is painted the way Google paints it, so a number
     * here means the same thing it means on the PSI page: under 50 red, 50-89
     * yellow, 90+ green. Failed checks (index 3) run the other way - more
     * failures is a better lead, not a worse site to avoid - so 3+ is green.
     * That inversion is deliberate and is why the two columns don't share a
     * palette.
     */
    private List<Request> auditConditionalFormatRules(int sheetId) {
        GridRange pagespeed = columnRange(sheetId, 2, 3);
        GridRange fails = columnRange(sheetId, 3, 4);
        return List.of(
                addConditionalFormat(pagespeed, "NUMBER_LESS", List.of("50"), null, SCORE_GREEN),
                addConditionalFormat(pagespeed, "NUMBER_BETWEEN", List.of("50", "89"), null, SCORE_YELLOW),
                addConditionalFormat(pagespeed, "NUMBER_GREATER_THAN_EQ", List.of("90"), null, SCORE_GRAY),
                addConditionalFormat(fails, "NUMBER_GREATER_THAN_EQ", List.of("3"), null, SCORE_GREEN),
                addConditionalFormat(fails, "NUMBER_BETWEEN", List.of("1", "2"), null, SCORE_YELLOW),
                addConditionalFormat(fails, "NUMBER_EQ", List.of("0"), null, SCORE_GRAY));
    }

    // Score column (index 10, "K"): >=70 green, 40-69 yellow, <40 gray.
    // Index follows HEADER - it shifted by one when "Контакт" was inserted
    // after "Тип телефона", and these rules must move with it or they paint
    // the wrong column.
    private List<Request> scoreConditionalFormatRules(int sheetId) {
        GridRange range = columnRange(sheetId, 10, 11);
        return List.of(
                addConditionalFormat(range, "NUMBER_GREATER_THAN_EQ", List.of("70"), null, SCORE_GREEN),
                addConditionalFormat(range, "NUMBER_BETWEEN", List.of("40", "69"), null, SCORE_YELLOW),
                addConditionalFormat(range, "NUMBER_LESS", List.of("40"), null, SCORE_GRAY));
    }

    // Status column (index 11, "L"): HOT red+bold, QUALIFIED orange, NEW gray.
    private List<Request> statusConditionalFormatRules(int sheetId) {
        GridRange range = columnRange(sheetId, 11, 12);
        return List.of(
                addConditionalFormat(range, "TEXT_EQ", List.of("HOT"), STATUS_RED, null),
                addConditionalFormat(range, "TEXT_EQ", List.of("QUALIFIED"), STATUS_ORANGE, null),
                addConditionalFormat(range, "TEXT_EQ", List.of("NEW"), STATUS_GRAY, null));
    }

    private Request addConditionalFormat(GridRange range, String conditionType, List<String> values,
                                          Color textColor, Color backgroundColor) {
        List<ConditionValue> conditionValues = values.stream()
                .map(v -> new ConditionValue().setUserEnteredValue(v))
                .toList();

        CellFormat format = new CellFormat();
        if (textColor != null) {
            format.setTextFormat(new TextFormat().setForegroundColor(textColor).setBold(true));
        }
        if (backgroundColor != null) {
            format.setBackgroundColor(backgroundColor);
        }

        ConditionalFormatRule rule = new ConditionalFormatRule()
                .setRanges(List.of(range))
                .setBooleanRule(new BooleanRule()
                        .setCondition(new BooleanCondition().setType(conditionType).setValues(conditionValues))
                        .setFormat(format));

        return new Request().setAddConditionalFormatRule(new AddConditionalFormatRuleRequest()
                .setRule(rule)
                .setIndex(0));
    }

    /** Header + one row per lead, in HEADER's column order. Package-visible for tests. */
    static List<List<Object>> buildRows(List<Lead> leads) {
        List<List<Object>> rows = new ArrayList<>();
        rows.add(HEADER);
        for (Lead lead : leads) {
            Company company = lead.getCompany();
            rows.add(List.of(
                    formatDate(lead.getCreatedAt()),
                    nullToEmpty(company.getName()),
                    nullToEmpty(lead.getNiche()),
                    nullToEmpty(company.getPhone()),
                    phoneTypeLabel(company),
                    contactPerson(company),
                    nullToEmpty(company.getEmail()),
                    nullToEmpty(company.getMessenger()),
                    nullToEmpty(company.getCity()),
                    sourceLabel(company.getSource()),
                    lead.getScore(),
                    lead.getStatus().name(),
                    displayReason(lead),
                    nullToEmpty(company.getSourceUrl())));
        }
        return rows;
    }

    /**
     * Header + one row per AUDITED company, worst sites first.
     *
     * <p>Only companies workers/site_audit has actually measured appear here.
     * A company that was never audited is left out entirely rather than shown
     * with blanks - an empty row would read as "site is fine", which is the
     * opposite of what an unmeasured site means.
     *
     * <p>Sorted by failure count then by PageSpeed ascending, so the top of
     * the sheet is the call list: the worse the site, the better the lead.
     * Package-visible for tests.
     */
    static List<List<Object>> buildAuditRows(List<Lead> leads) {
        List<List<Object>> rows = new ArrayList<>();
        rows.add(AUDIT_HEADER);

        leads.stream()
                .filter(lead -> auditAttempted(lead.getCompany()))
                .sorted(Comparator
                        .comparing((Lead lead) -> auditInt(lead.getCompany(), "auditFails"),
                                Comparator.nullsLast(Comparator.reverseOrder()))
                        .thenComparing(lead -> auditInt(lead.getCompany(), "pagespeed"),
                                Comparator.nullsLast(Comparator.naturalOrder())))
                .forEach(lead -> {
                    Company company = lead.getCompany();
                    rows.add(List.of(
                            nullToEmpty(company.getName()),
                            nullToEmpty(company.getDomain()),
                            orEmpty(auditInt(company, "pagespeed")),
                            orEmpty(auditInt(company, "auditFails")),
                            failedChecks(company),
                            nullToEmpty(company.getPhone()),
                            nullToEmpty(company.getEmail()),
                            nullToEmpty(company.getCity()),
                            sourceLabel(company.getSource()),
                            lead.getScore(),
                            lead.getStatus().name(),
                            auditedAt(company)));
                });
        return rows;
    }

    /**
     * A named person to ask for, from whichever source knows one.
     *
     * <p>Two producers, checked in that order:
     * {@code raw.contact_person} - the responsible official named on a
     * госзакупка notice, which the customer is required by law to publish -
     * and {@code raw.ceo_name}/{@code raw.ceo_post} from ЕГРЮЛ via
     * workers/egrul. The procurement one wins when both exist: it names the
     * person running THIS purchase, while the CEO is merely whoever signs.
     *
     * <p>The post is included when known ("Генеральный директор Иванов И.И.")
     * because it tells a caller how to open, not just whom to name.
     */
    private static String contactPerson(Company company) {
        if (company.getRaw() == null) {
            return "";
        }
        Object responsible = company.getRaw().get("contact_person");
        if (responsible != null && !String.valueOf(responsible).isBlank()) {
            return String.valueOf(responsible).trim();
        }

        Object ceo = company.getRaw().get("ceo_name");
        if (ceo == null || String.valueOf(ceo).isBlank()) {
            return "";
        }
        Object post = company.getRaw().get("ceo_post");
        return post == null || String.valueOf(post).isBlank()
                ? String.valueOf(ceo).trim()
                : String.valueOf(post).trim() + " " + String.valueOf(ceo).trim();
    }

    /**
     * A site the studio could be paid to fix: measured, and bad enough to
     * talk about. Either three or more failed checks, or a PageSpeed score
     * Google itself paints red.
     *
     * <p>Unmeasured sites are excluded deliberately. "Nobody looked" is not
     * "needs work", and a caller who opens with a complaint about a site that
     * is actually fine has lost the call in the first sentence.
     */
    private static boolean siteNeedsWork(Lead lead) {
        Company company = lead.getCompany();
        Integer fails = auditInt(company, "auditFails");
        if (fails != null && fails >= 3) {
            return true;
        }
        Integer pagespeed = auditInt(company, "pagespeed");
        return pagespeed != null && pagespeed < 50;
    }

    private static boolean auditAttempted(Company company) {
        return company.getRaw() != null
                && Boolean.TRUE.equals(company.getRaw().get("audit_attempted"));
    }

    private static Integer auditInt(Company company, String key) {
        if (company.getRaw() == null) {
            return null;
        }
        Object value = company.getRaw().get(key);
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return value == null ? null : Integer.valueOf(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** Empty string rather than 0 - a site nobody could measure is not a zero. */
    private static Object orEmpty(Integer value) {
        return value == null ? "" : value;
    }

    /** raw.audit_failed_checks as a comma-separated list for the cell. */
    private static String failedChecks(Company company) {
        if (company.getRaw() == null) {
            return "";
        }
        Object value = company.getRaw().get("audit_failed_checks");
        if (value instanceof Collection<?> collection) {
            return collection.stream().map(String::valueOf).collect(Collectors.joining(", "));
        }
        return value == null ? "" : String.valueOf(value);
    }

    private static String auditedAt(Company company) {
        if (company.getRaw() == null) {
            return "";
        }
        Object value = company.getRaw().get("audit_at");
        if (value == null) {
            return "";
        }
        try {
            return DATE_FORMAT.format(Instant.parse(String.valueOf(value)));
        } catch (RuntimeException e) {
            return String.valueOf(value);
        }
    }

    private static boolean isWarm(LeadStatus status) {
        return status == LeadStatus.HOT || status == LeadStatus.QUALIFIED;
    }

    /** Friendly Russian tab/column label for a source - also used to name per-source tabs. */
    static String sourceLabel(LeadSource source) {
        return switch (source) {
            case TELEGRAM_ORDER -> "TG заявки";
            case INSTAGRAM_CTA -> "Instagram";
            case GOOGLE_MAPS -> "Google Maps";
            case TWOGIS -> "2GIS";
            case OSM -> "OpenStreetMap";
            case ZAKUPKI -> "Госзакупки";
            case NEW_DOMAIN -> "Новые домены";
            case YANDEX_REVIEW -> "Яндекс Отзывы";
            case VACANCY -> "Вакансии";
            case HH -> "hh.ru";
            case AVITO_JOB -> "Avito";
            case DEMO -> "Демо";
            case OTHER -> "Прочее";
        };
    }

    /**
     * Friendly Russian label for the "Тип телефона" column, from the
     * {@code primary_phone_type} raw flag PhoneUtil sets during ingest
     * (see IngestService). Blank when no phone was ever classified - most
     * commonly because the company has no phone at all.
     */
    private static String phoneTypeLabel(Company company) {
        Object rawType = company.getRaw() == null ? null : company.getRaw().get("primary_phone_type");
        if (rawType == null) {
            return "";
        }
        return switch (String.valueOf(rawType)) {
            case "MOBILE" -> "Мобильный";
            case "CITY_MOSCOW" -> "Городской Мск";
            case "CITY_SPB", "CITY_OTHER" -> "Городской";
            case "TOLL_FREE" -> "8-800";
            default -> "";
        };
    }

    /**
     * The "Причина" column: the scoring reasons (lead.getHotReason()) plus,
     * separated by " | ", short context that has no column of its own -
     * the purchase subject/budget for госзакупки leads, and where a contact
     * came from when workers/enrich found it on the company's site. Keeps the
     * sheet self-explanatory without adding columns that would be blank for
     * most rows.
     */
    static String displayReason(Lead lead) {
        Company company = lead.getCompany();
        String reason = nullToEmpty(lead.getHotReason());
        reason = appendSection(reason, zakupkiSummary(company));
        reason = appendSection(reason, enrichmentSummary(company));
        return reason;
    }

    private static String appendSection(String reason, String section) {
        if (section == null) {
            return reason;
        }
        return reason.isEmpty() ? section : reason + " | " + section;
    }

    /** "Госзакупка: <subject>, бюджет N ₽" from raw.subject/raw.budget - ZAKUPKI leads only, null otherwise. */
    private static String zakupkiSummary(Company company) {
        if (company.getSource() != LeadSource.ZAKUPKI || company.getRaw() == null) {
            return null;
        }

        Object subject = company.getRaw().get("subject");
        Object budget = company.getRaw().get("budget");
        if (subject == null && budget == null) {
            return null;
        }

        StringBuilder summary = new StringBuilder("Госзакупка: ");
        summary.append(subject != null ? subject : "предмет не указан");
        if (budget != null) {
            summary.append(", бюджет ").append(formatBudget(budget)).append(" ₽");
        }
        return summary.toString();
    }

    /**
     * "Контакт с сайта: email, телефон (email с /contacts; ...)" when
     * workers/enrich filled at least one field (raw.enrich_filled, written by
     * ContactEnrichmentService), so a manager knows the email wasn't reported
     * by the source itself. Null when enrichment filled nothing - an attempt
     * that found nothing is not worth a note in the sheet.
     */
    static String enrichmentSummary(Company company) {
        if (company.getRaw() == null) {
            return null;
        }
        Object filled = company.getRaw().get(ContactEnrichmentService.RAW_ENRICH_FILLED);
        if (!(filled instanceof Collection<?> fields) || fields.isEmpty()) {
            return null;
        }

        String labels = fields.stream()
                .map(field -> switch (String.valueOf(field)) {
                    case ContactEnrichmentService.FIELD_PHONE -> "телефон";
                    case ContactEnrichmentService.FIELD_MESSENGER -> "мессенджер";
                    default -> String.valueOf(field);
                })
                .collect(Collectors.joining(", "));

        StringBuilder summary = new StringBuilder("Контакт с сайта: ").append(labels);
        Object notes = company.getRaw().get(ContactEnrichmentService.RAW_ENRICH_NOTES);
        if (notes != null && !String.valueOf(notes).isBlank()) {
            summary.append(" (").append(notes).append(")");
        }
        return summary.toString();
    }

    /** "8398003.33" (or any Number) -> "8 398 003" - space-grouped, no decimals (a spreadsheet cell doesn't need kopecks). */
    static String formatBudget(Object budgetValue) {
        if (!(budgetValue instanceof Number number)) {
            return String.valueOf(budgetValue);
        }
        long rounded = Math.round(number.doubleValue());
        String digits = Long.toString(Math.abs(rounded));
        StringBuilder grouped = new StringBuilder();
        int count = 0;
        for (int i = digits.length() - 1; i >= 0; i--) {
            grouped.append(digits.charAt(i));
            count++;
            if (count % 3 == 0 && i != 0) {
                grouped.append(' ');
            }
        }
        String result = grouped.reverse().toString();
        return rounded < 0 ? "-" + result : result;
    }

    private static String formatDate(Instant instant) {
        return instant == null ? "" : DATE_FORMAT.format(instant);
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String quoted(String sheetTitle) {
        return "'" + sheetTitle.replace("'", "''") + "'";
    }

    private static GridRange fullWidthRange(int sheetId, int startRow, int endRow) {
        return new GridRange().setSheetId(sheetId)
                .setStartRowIndex(startRow).setEndRowIndex(endRow)
                .setStartColumnIndex(0).setEndColumnIndex(COLUMN_COUNT);
    }

    private static GridRange columnRange(int sheetId, int startCol, int endCol) {
        return new GridRange().setSheetId(sheetId)
                .setStartRowIndex(1).setEndRowIndex(MAX_FORMAT_ROWS)
                .setStartColumnIndex(startCol).setEndColumnIndex(endCol);
    }

    /** Explicit on both axes - the audit tab is narrower than the lead tabs. */
    private static GridRange columnRange(int sheetId, int startCol, int endCol, int startRow, int endRow) {
        return new GridRange().setSheetId(sheetId)
                .setStartRowIndex(startRow).setEndRowIndex(endRow)
                .setStartColumnIndex(startCol).setEndColumnIndex(endCol);
    }

    private static Color rgb(double r, double g, double b) {
        return new Color().setRed((float) r).setGreen((float) g).setBlue((float) b);
    }

    private static Sheets buildSheetsService(String credentialsPath) throws IOException, GeneralSecurityException {
        GoogleCredentials credentials;
        try (FileInputStream in = new FileInputStream(credentialsPath)) {
            credentials = GoogleCredentials.fromStream(in).createScoped(List.of(SheetsScopes.SPREADSHEETS));
        }
        HttpRequestInitializer requestInitializer = new HttpCredentialsAdapter(credentials);
        return new Sheets.Builder(GoogleNetHttpTransport.newTrustedTransport(), GsonFactory.getDefaultInstance(), requestInitializer)
                .setApplicationName(APPLICATION_NAME)
                .build();
    }

    public record SyncResult(boolean enabled, int tabsUpdated, int leadsExported, String message) {
    }
}
