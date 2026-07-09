SELECT Student.ime, Predmet.naziv, Stipendija.iznos
FROM Student, Ispit, Predmet, Stipendija
WHERE Student.indeks = Ispit.studentIndeks
  AND Ispit.predmetId = Predmet.predmetId
  AND Stipendija.studentIndeks = Student.indeks
  AND (Ispit.ocena = 9 OR Ispit.ocena = 10)
  AND Predmet.espb > 4;
