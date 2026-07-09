SELECT Student.ime, Ispit.ocena
FROM Student, Ispit
WHERE Student.indeks = Ispit.studentIndeks AND Ispit.ocena >= 8;
