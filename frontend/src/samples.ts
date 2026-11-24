export const samples: { label: string; sql: string }[] = [
  {
    label: 'Visits for one animal',
    sql: `SELECT *
FROM visits
WHERE animal_id = 4242`,
  },
  {
    label: 'Owner by email',
    sql: `SELECT *
FROM owners
WHERE lower(email) = 'logan.miller104729@gmail.com'`,
  },
  {
    label: "A clinic's week of treatments",
    sql: `SELECT t.id, t.drug_id, t.dose_mg, v.visited_at
FROM treatments t
JOIN visits v ON v.id = t.visit_id
WHERE v.clinic_id = 42
  AND v.visited_at >= '2026-03-02'
  AND v.visited_at < '2026-03-09'`,
  },
  {
    label: 'Newest open visits',
    sql: `SELECT *
FROM visits
WHERE status = 'open'
ORDER BY visited_at DESC
LIMIT 20`,
  },
  {
    label: 'Horses with no vaccine this year',
    sql: `SELECT a.id, a.name, a.breed
FROM animals a
WHERE a.species = 'horse'
  AND a.id NOT IN (
    SELECT v.animal_id
    FROM visits v
    JOIN treatments t ON t.visit_id = v.id
    JOIN drugs d ON d.id = t.drug_id
    WHERE d.category = 'vaccine'
      AND t.administered_at > now() - interval '1 year'
  )`,
  },
  {
    label: 'Top owners by fees, last 90 days',
    sql: `SELECT o.id, o.full_name, o.city,
  count(*) AS visits,
  sum(v.fee) AS total_fees
FROM owners o
JOIN animals a ON a.owner_id = o.id
JOIN visits v ON v.animal_id = a.id
WHERE v.visited_at >= now() - interval '90 days'
  AND v.status = 'completed'
GROUP BY o.id, o.full_name, o.city
ORDER BY total_fees DESC
LIMIT 10`,
  },
  {
    label: "Owner's pets with last visit",
    sql: `SELECT a.id, a.name, a.species, a.breed,
  (SELECT max(v.visited_at) FROM visits v WHERE v.animal_id = a.id) AS last_visit,
  (SELECT count(*) FROM visits v WHERE v.animal_id = a.id) AS visit_count
FROM animals a
WHERE a.owner_id = 114542
ORDER BY last_visit DESC`,
  },
  {
    label: 'Portland Maine Coons, last seen',
    sql: `WITH ranked AS (
  SELECT a.id AS animal_id, a.name, o.full_name AS owner, o.phone,
    v.clinic_id, v.visited_at, v.reason,
    row_number() OVER (PARTITION BY a.id ORDER BY v.visited_at DESC) AS rn
  FROM animals a
  JOIN owners o ON o.id = a.owner_id
  JOIN visits v ON v.animal_id = a.id
  WHERE a.breed = 'Maine Coon'
    AND o.city = 'Portland'
)
SELECT r.animal_id, r.name, r.owner, r.phone,
  c.name AS clinic, r.visited_at, r.reason
FROM ranked r
JOIN clinics c ON c.id = r.clinic_id
WHERE r.rn = 1
ORDER BY r.visited_at
LIMIT 50`,
  },
  {
    label: "Clinic day or one owner's visits",
    sql: `SELECT v.id, v.animal_id, v.clinic_id, v.visited_at, v.reason, v.status
FROM visits v
JOIN animals a ON a.id = v.animal_id
WHERE (v.clinic_id = 42
    AND v.visited_at >= '2026-03-14'
    AND v.visited_at < '2026-03-15')
  OR a.owner_id = 114542
ORDER BY v.visited_at DESC`,
  },
  {
    label: 'Antibiotic doses by species, 6 months',
    sql: `SELECT a.species,
  date_trunc('month', t.administered_at) AS month,
  count(*) AS doses,
  round(sum(t.dose_mg) / 1000, 1) AS total_g
FROM treatments t
JOIN drugs d ON d.id = t.drug_id
JOIN visits v ON v.id = t.visit_id
JOIN animals a ON a.id = v.animal_id
WHERE d.category = 'antibiotic'
  AND t.administered_at >= date_trunc('month', now()) - interval '6 months'
GROUP BY a.species, month
ORDER BY month, a.species`,
  },
]
