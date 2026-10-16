from django.db import models

from django.db import models
from user_module.models import SellerProfile


class Store(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('ACTIVE', 'Active'),
        ('SUSPENDED', 'Suspended'),
    )

    seller = models.OneToOneField(
        SellerProfile,
        on_delete=models.CASCADE
    )

    store_name = models.CharField(max_length=150)

    logo = models.ImageField(
        upload_to='store_logos/',
        blank=True,
        null=True
    )

    banner = models.ImageField(
        upload_to='store_banners/',
        blank=True,
        null=True
    )

    description = models.TextField(blank=True)
    address = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
       return self.store_name



class VerificationDocument(models.Model):

    DOCUMENT_TYPE_CHOICES = (
        ('ID_PROOF', 'ID Proof'),
        ('ADDRESS_PROOF', 'Address Proof'),
        ('BUSINESS_PROOF', 'Business Proof'),
        ('TAX_DOCUMENT', 'Tax Document'),
    )

    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    )

    seller = models.ForeignKey(
        SellerProfile,
        on_delete=models.CASCADE
    )

    document_type = models.CharField(
        max_length=30,
        choices=DOCUMENT_TYPE_CHOICES
    )

    document = models.FileField(
        upload_to='seller_documents/'
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING'
    )

    rejection_reason = models.TextField( blank=True )

    uploaded_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        return f"{self.seller.user.email} - {self.get_document_type_display()}"


class BankDetails(models.Model):

    seller = models.OneToOneField(
        SellerProfile,
        on_delete=models.CASCADE
    )

    account_holder_name = models.CharField(
        max_length=150
    )

    account_number = models.CharField(
        max_length=30
    )

    ifsc_code = models.CharField(
        max_length=20
    )

    bank_name = models.CharField(
        max_length=150
    )

    branch_name = models.CharField(
        max_length=150,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        return f"{self.seller.user.email} - {self.bank_name}"

