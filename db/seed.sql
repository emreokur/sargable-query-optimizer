CREATE EXTENSION IF NOT EXISTS hypopg;

CREATE TABLE clinics (id int PRIMARY KEY, name text, city text);
CREATE TABLE owners (id int PRIMARY KEY, full_name text, email text, phone text, city text, created_at timestamptz);
CREATE TABLE animals (id int PRIMARY KEY, owner_id int, name text, species text, breed text, sex text, birth_date date, weight_kg numeric(5,1));
CREATE TABLE drugs (id int PRIMARY KEY, name text, category text);
CREATE TABLE visits (id int PRIMARY KEY, animal_id int, clinic_id int, visited_at timestamptz, reason text, status text, fee numeric(8,2));
CREATE TABLE treatments (id int PRIMARY KEY, visit_id int, drug_id int, dose_mg numeric(7,2), administered_at timestamptz);

CREATE FUNCTION pick(text[]) RETURNS text LANGUAGE sql AS 'SELECT $1[1 + floor(random() * cardinality($1))::int]';

SELECT setseed(0.42);

INSERT INTO clinics
SELECT g,
  pick(ARRAY['Riverside','Oakwood','Maple Grove','Northside','Lakeview','Hillcrest','Willow Creek','Cedar Park','Harbor','Sunnyvale','Greenfield','Brookside','Pinecrest','Westgate','Fairview'])
    || ' ' || pick(ARRAY['Animal Hospital','Veterinary Clinic','Pet Care Center','Vet Practice','Animal Clinic','Veterinary Group']),
  pick(ARRAY['Portland','Seattle','Denver','Austin','Boston','Chicago','Minneapolis','Atlanta','Phoenix','San Diego','Nashville','Raleigh','Madison','Boise','Tucson','Omaha','Richmond','Salt Lake City','Pittsburgh','Columbus'])
FROM generate_series(1, 200) g;

INSERT INTO owners
SELECT g, first || ' ' || last,
  CASE WHEN random() < 0.15 THEN upper(first) ELSE first END || '.' || last || g || '@'
    || pick(ARRAY['gmail.com','Gmail.com','outlook.com','Outlook.com','yahoo.com','iCloud.com','hotmail.com','proton.me']),
  format('(%s) 555-%s', 200 + floor(random() * 800)::int, lpad(floor(random() * 10000)::text, 4, '0')),
  pick(ARRAY['Portland','Seattle','Denver','Austin','Boston','Chicago','Minneapolis','Atlanta','Phoenix','San Diego','Nashville','Raleigh','Madison','Boise','Tucson','Omaha','Richmond','Salt Lake City','Pittsburgh','Columbus']),
  now() - random() * interval '8 years'
FROM (
  SELECT g,
    pick(ARRAY['Emma','Liam','Olivia','Noah','Ava','James','Sophia','Lucas','Mia','Ethan','Isabella','Mason','Amelia','Logan','Harper','Elijah','Ella','Aiden','Grace','Jack','Chloe','Daniel','Nora','Henry','Zoe','Samuel','Lily','Owen','Hannah','Leo']) AS first,
    pick(ARRAY['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis','Martinez','Lopez','Wilson','Anderson','Thomas','Taylor','Moore','Jackson','Martin','Lee','Thompson','White','Harris','Clark','Lewis','Walker','Hall','Young','King','Wright','Scott','Nguyen']) AS last
  FROM generate_series(1, 150000) g
) s;

INSERT INTO animals
SELECT g, 1 + floor(random() * 150000)::int,
  pick(ARRAY['Bella','Max','Luna','Charlie','Lucy','Cooper','Daisy','Milo','Bailey','Oliver','Coco','Rocky','Nala','Simba','Loki','Teddy','Willow','Ruby','Leo','Pepper','Ginger','Oscar','Zeus','Rosie','Bear','Mochi','Biscuit','Shadow','Jasper','Cleo','Hazel','Finn','Olive','Thor','Maple','Ziggy','Sunny','Kiwi','Spike','Duke']),
  species,
  CASE species
    WHEN 'dog' THEN pick(ARRAY['Labrador Retriever','German Shepherd','Golden Retriever','French Bulldog','Beagle','Poodle','Dachshund','Border Collie','Boxer','Cocker Spaniel','Shih Tzu','Mixed Breed'])
    WHEN 'cat' THEN pick(ARRAY['Domestic Shorthair','Domestic Longhair','Siamese','Maine Coon','British Shorthair','Persian','Ragdoll','Bengal','Sphynx'])
    WHEN 'rabbit' THEN pick(ARRAY['Holland Lop','Netherland Dwarf','Mini Rex','Lionhead','Flemish Giant'])
    WHEN 'horse' THEN pick(ARRAY['Thoroughbred','Quarter Horse','Arabian','Friesian','Shetland Pony','Warmblood'])
    WHEN 'bird' THEN pick(ARRAY['Budgerigar','Cockatiel','African Grey','Canary','Lovebird','Cockatoo'])
    ELSE pick(ARRAY['Bearded Dragon','Leopard Gecko','Ball Python','Corn Snake','Red-eared Slider','Green Iguana'])
  END,
  pick(ARRAY['male','female']),
  current_date - floor(random() * 15 * 365)::int,
  round((CASE species
    WHEN 'dog' THEN 3 + random() * 42
    WHEN 'cat' THEN 2.5 + random() * 5
    WHEN 'rabbit' THEN 1 + random() * 4
    WHEN 'horse' THEN 350 + random() * 300
    WHEN 'bird' THEN 0.1 + random() * 1.1
    ELSE 0.1 + random() * 5
  END)::numeric, 1)
FROM (
  SELECT g, CASE
    WHEN r < 0.48 THEN 'dog' WHEN r < 0.84 THEN 'cat' WHEN r < 0.92 THEN 'rabbit'
    WHEN r < 0.96 THEN 'bird' WHEN r < 0.9996 THEN 'reptile' ELSE 'horse' END AS species
  FROM (SELECT g, random() AS r FROM generate_series(1, 400000) g) s
) s;

INSERT INTO drugs
SELECT g,
  CASE WHEN g <= 500
    THEN pick(ARRAY['Rabies Vaccine','DHPP Vaccine','Bordetella Vaccine','Leptospirosis Vaccine','FVRCP Vaccine','Feline Leukemia Vaccine','Myxomatosis Vaccine','Equine Influenza Vaccine','Tetanus Toxoid','Lyme Vaccine']) || ' ' || (1 + g % 3) || ' ml'
    ELSE (CASE (g - 501) / 300
      WHEN 0 THEN pick(ARRAY['Amoxicillin','Clavamox','Doxycycline','Enrofloxacin','Cephalexin','Metronidazole','Clindamycin'])
      WHEN 1 THEN pick(ARRAY['Milbemycin','Selamectin','Fenbendazole','Praziquantel','Ivermectin','Fluralaner','Pyrantel'])
      WHEN 2 THEN pick(ARRAY['Buprenorphine','Tramadol','Gabapentin','Methadone','Butorphanol'])
      WHEN 3 THEN pick(ARRAY['Meloxicam','Carprofen','Robenacoxib','Prednisolone','Dexamethasone','Firocoxib'])
      ELSE pick(ARRAY['Dexmedetomidine','Acepromazine','Trazodone','Midazolam','Ketamine','Alfaxalone'])
    END) || ' ' || (5 * (1 + g % 40)) || ' mg'
  END,
  CASE WHEN g <= 500 THEN 'vaccine'
    ELSE (ARRAY['antibiotic','antiparasitic','analgesic','anti-inflammatory','sedative'])[1 + (g - 501) / 300] END
FROM generate_series(1, 2000) g;

INSERT INTO visits
SELECT g, 1 + floor(400000 * random() ^ 1.5)::int, 1 + floor(random() * 200)::int,
  now() - interval '5 years' * random() ^ 2,
  reason,
  CASE WHEN r < 0.02 THEN 'open' WHEN r < 0.05 THEN 'scheduled' WHEN r < 0.07 THEN 'cancelled' ELSE 'completed' END,
  round((CASE reason WHEN 'surgery' THEN 400 + random() * 1600 WHEN 'dental' THEN 150 + random() * 450 ELSE 40 + random() * 160 END)::numeric, 2)
FROM (
  SELECT g, random() AS r,
    pick(ARRAY['vaccination','vaccination','vaccination','vaccination','vaccination','vaccination','checkup','checkup','checkup','checkup',
               'skin','skin','digestive','digestive','injury','injury','dental','surgery','lameness','follow-up']) AS reason
  FROM generate_series(1, 1500000) g
) s;

INSERT INTO treatments
SELECT s.g, s.visit_id,
  CASE WHEN v.reason = 'vaccination' THEN 1 + floor(random() * 500)::int ELSE 501 + floor(random() * 1500)::int END,
  round((0.5 + random() * 500)::numeric, 2),
  v.visited_at + random() * interval '2 hours'
FROM (SELECT g, 1 + floor(random() * 1500000)::int AS visit_id FROM generate_series(1, 3000000) g) s
JOIN visits v ON v.id = s.visit_id;

DROP FUNCTION pick(text[]);

VACUUM ANALYZE;

CREATE ROLE demo_readonly LOGIN PASSWORD 'demo_readonly';
GRANT pg_read_all_data TO demo_readonly;
