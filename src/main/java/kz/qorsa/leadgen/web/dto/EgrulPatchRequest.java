package kz.qorsa.leadgen.web.dto;

import jakarta.validation.constraints.Size;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * One ЕГРЮЛ lookup result from workers/egrul.
 *
 * <p>Every field is optional: a lookup that matched no company still reports
 * back, so the attempt is recorded and the same name isn't queried again on
 * every run. What it never carries is a personal phone or address - the
 * registry publishes who runs a company, and that is all this stores.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class EgrulPatchRequest {

    /** ИНН as matched in the registry. */
    @Size(max = 12)
    private String inn;

    /** ОГРН, kept for traceability back to the registry entry. */
    @Size(max = 15)
    private String ogrn;

    /** Full legal name as the registry spells it. */
    @Size(max = 300)
    private String legalName;

    /** ФИО руководителя. */
    @Size(max = 150)
    private String ceoName;

    /**
     * Должность: "Генеральный директор", "Директор", "Президент".
     *
     * <p>Generous on purpose. The register holds titles like "Руководитель
     * территориального органа Федерального казначейства по ..." - a 100-char
     * cap rejected 40 of 598 lookups outright, losing the CEO's name over the
     * length of their job title.
     */
    @Size(max = 300)
    private String ceoPost;

    /** Free-form note: why nothing matched, or which of several candidates was taken. */
    @Size(max = 300)
    private String egrulNotes;
}
