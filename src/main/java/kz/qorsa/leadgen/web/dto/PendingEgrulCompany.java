package kz.qorsa.leadgen.web.dto;

import java.util.UUID;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

/**
 * One company handed to workers/egrul by GET /api/v1/companies/pending-egrul.
 *
 * <p>{@code inn} is non-null only when a госзакупка notice already carried
 * one; the worker looks up by ИНН when it can (exact) and falls back to
 * name + city (a guess that has to be scored).
 */
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class PendingEgrulCompany {

    private UUID id;
    private String name;
    private String city;
    private String inn;
}
