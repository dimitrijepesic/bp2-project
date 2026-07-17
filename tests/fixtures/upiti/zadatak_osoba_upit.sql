SELECT Osoba.Ime
FROM Osoba, Koristi, Automobil
WHERE Osoba.IdOso = Koristi.IdOso AND Koristi.IdAut = Automobil.IdAut AND Automobil.Tablica = 'BG-234-JR';
